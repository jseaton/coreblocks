from amaranth import *
from amaranth.lib.data import StructLayout
from amaranth.lib.wiring import Component, In, Out
from amaranth.lib.enum import Enum, auto

from coreblocks.params.genparams import GenParams
from coreblocks.interface.layouts import *

from transactron.utils import DependencyContext
from transactron.core import Transaction, TModule
from transactron.utils.amaranth_ext.component_interface import ComponentInterface, CIn, COut
from transactron import *

from coreblocks.peripherals.bus_adapter import BusMasterInterface
from coreblocks.interface.keys import (
    CommonBusDataKey,
    FlushICacheKey
)

class CoreState(Enum):
    NONEXISTENT = auto()
    UNAVAIL = auto()
    HALTED = auto()
    RUNNING = auto()

ABITS = 16 # TODO make smaller...

# Suggested DMI from A.3 of debug spec, used by rocket
class DebugModuleInterface(ComponentInterface):
    def __init__(self):
        self.req_ready = COut(1)
        self.req_valid = CIn(1)
        self.req_op = CIn(2)
        self.req_address = CIn(ABITS)
        self.req_data = CIn(32)

        self.rsp_ready = CIn(1)
        self.rsp_valid = COut(1)
        self.rsp_op = COut(2)
        self.rsp_data = COut(32)

class DebugModule(Component):
    dmi: DebugModuleInterface
    bus: BusMasterInterface

    halt: Required[Method]
    exec: Required[Method]

    rf_read_req: Required[Methods]
    rf_read_resp: Required[Methods]

    def __init__(self, gen_params: GenParams):
        super().__init__({"dmi": Out(DebugModuleInterface().signature)})

        self.gen_params = gen_params

        dm = DependencyContext.get()
        self.bus = dm.get_dependency(CommonBusDataKey())

        self.dmactive = Signal()
        self.ndmreset = Signal()

        self.core_state = Signal(CoreState, init=CoreState.RUNNING)

        self.hartsel = Signal(1)


        self.abstract_type = Signal(8)
        self.abstract_control = Signal(24)

        self.abstract_busy = Signal()
        self.abstract_cmderr = Signal(3)

        self.abstract_data = Array([Signal(32)]*2) # yes, just the two hopefully

        # TODO move layouts
        self.dmcontrol_layout = StructLayout({
            "dmactive": 1,
            "ndmreset": 1,
            "clrresethaltreq":  1,
            "setresethaltreq": 1,
            "clrkeepalive" : 1,
            "setkeepalive" : 1,
            "hartselhi" : 10,
            "hartsello" : 10,
            "hasel" : 1,
            "ackunavail" : 1,
            "ackhavereset" : 1,
            "hartreset" : 1,
            "resumereq" : 1,
            "haltreq" : 1,
        })

        self.dmstatus_layout = StructLayout({
            "version": 4,
            "confstrptrvalid": 1,
            "hasresethaltreq":  1,
            "authbusy": 1,
            "authenticated" : 1,
            "anyhalted" : 1,
            "allhalted" : 1,
            "anyrunning" : 1,
            "allrunning" : 1,
            "anyunavail" : 1,
            "allunavail" : 1,
            "anynonexistent" : 1,
            "allnonexistent" : 1,
            "anyresumeack" : 1,
            "allresumeack" : 1,
            "anyhavereset" : 1,
            "allhavereset" : 1,
            "0" : 2,
            "impebreak" : 1,
            "stickyunavail" : 1,
            "ndmresetpending" : 1,
        })

        self.abstractcs_layout = StructLayout({
            "datacount": 4,
            "0_0": 4,
            "cmderr":  3,
            "relaxedpriv": 1,
            "busy" : 1,
            "0_1" : 11,
            "progbufsize" : 5,
        })

        self.command_layout = StructLayout({
            "control" : len(self.abstract_control),
            "cmdtype" : len(self.abstract_type)
            })

        self.access_register_layout = StructLayout({
            "regno": 16,
            "write": 1,
            "transfer": 1,
            "postexec": 1,
            "aarpostincrement" : 1,
            "aarsize" : 3,
            "0": 1,
            "cmdtype" : 8,
        })

        self.access_memory_layout = StructLayout({
            "0_0": 14,
            "target-specific": 2,
            "write": 1,
            "0_1": 2,
            "aampostincrement" : 1,
            "aamsize" : 3,
            "aamvirtual": 1,
            "cmdtype" : 8,
        })

        layouts = self.gen_params.get(FetchLayouts)

        self.halt = Method()
        self.exec = Method(i=layouts.resume)
        self.flush_icache = dm.get_dependency(FlushICacheKey())

        # TODO just use one
        self.rf_read_req = Methods(2 * gen_params.frontend_superscalarity, i=gen_params.get(RFLayouts).rf_read_in)
        self.rf_read_resp = Methods(
            2 * gen_params.frontend_superscalarity,
            i=gen_params.get(RFLayouts).rf_read_in,
            o=gen_params.get(RFLayouts).rf_read_out,
        )

    def read(self, m, address, rsp_op, rsp_data):
        with m.Switch(address):
            with m.Case(0x4): # data0
                m.d.av_comb += rsp_op.eq(0) # TODO urgh such a mess of comb and sync
                m.d.sync += rsp_data.eq(self.abstract_data[0])
                m.next = "RESP_WAITING"
            with m.Case(0x5): # data1
                m.d.av_comb += rsp_op.eq(0)
                m.d.sync += rsp_data.eq(self.abstract_data[1])
                m.next = "RESP_WAITING"
            with m.Case(0x10): # dmcontrol
                m.d.av_comb += rsp_op.eq(0)
                resp = Signal(self.dmcontrol_layout)
                m.d.av_comb += [
                        resp.dmactive.eq(self.dmactive),
                        resp.ndmreset.eq(self.ndmreset),
                        resp.hartsello.eq(self.hartsel[:10]),
                        resp.hartselhi.eq(self.hartsel[10:])
                        ]
                m.d.sync += rsp_data.eq(resp)
                m.next = "RESP_WAITING"
            with m.Case(0x11): # dmstatus
                m.d.av_comb += rsp_op.eq(0)
                resp = Signal(self.dmstatus_layout)
                m.d.av_comb += [
                        resp.version.eq(3),
                        resp.authenticated.eq(1),
                        resp.anyhalted.eq((self.hartsel == 0).bool() & (self.core_state == CoreState.HALTED).bool()), # NOTE this code is only correct for single core!
                        resp.allhalted.eq((self.hartsel == 0).bool()  & (self.core_state == CoreState.HALTED).bool()),
                        resp.anyrunning.eq((self.hartsel == 0).bool()  & (self.core_state == CoreState.RUNNING).bool()),
                        resp.allrunning.eq((self.hartsel == 0).bool()  & (self.core_state == CoreState.RUNNING).bool()),
                        resp.anyunavail.eq((self.hartsel == 0).bool()  & (self.core_state == CoreState.UNAVAIL).bool()),
                        resp.allunavail.eq((self.hartsel == 0).bool()  & (self.core_state == CoreState.UNAVAIL).bool()),
                        resp.anynonexistent.eq(self.hartsel != 0),
                        resp.allnonexistent.eq(self.hartsel != 0),
                        ]
                m.d.sync += rsp_data.eq(resp)
                m.next = "RESP_WAITING"
            with m.Case(0x16): # abstractcs
                resp = Signal(self.abstractcs_layout)
                m.d.av_comb += [
                        resp.datacount.eq(2),
                        resp.cmderr.eq(self.abstract_cmderr),
                        resp.busy.eq(self.abstract_busy),
                        resp.progbufsize.eq(16)
                        ]
                m.d.sync += rsp_data.eq(resp)
                m.next = "RESP_WAITING"
            with m.Case(*range(0x20,0x30)): #progbuf
                read_pending = Signal()
                with m.If(~read_pending):
                    with Transaction().body(m):
                        self.bus.request_read(m, addr=(address - 0x20)*4 + 0x1000, sel=0xf)
                        m.d.sync += read_pending.eq(1)
                with m.Else():
                    with Transaction().body(m):
                        fetched = self.bus.get_read_response(m)
                        m.d.sync += rsp_data.eq(fetched.data)
                        m.d.av_comb += rsp_op.eq(0) # TODO busy stuff
                        m.d.sync += read_pending.eq(0)
                        m.next = "RESP_WAITING"

            with m.Default():
                m.d.av_comb += rsp_op.eq(0)
                m.d.sync += rsp_data.eq(0)
                m.next = "RESP_WAITING"

    def write(self, m, address, data, rsp_op):
        with m.Switch(address):
            with m.Case(0x4): # data0
                m.d.av_comb += rsp_op.eq(0)
                m.d.sync += self.abstract_data[0].eq(data)
                m.next = "RESP_WAITING"
            with m.Case(0x5): # data1
                m.d.av_comb += rsp_op.eq(0)
                m.d.sync += self.abstract_data[1].eq(data)
                m.next = "RESP_WAITING"
            with m.Case(0x10): # dmcontrol
                m.d.av_comb += rsp_op.eq(0)
                req = Signal(self.dmcontrol_layout)
                m.d.av_comb += [
                        req.eq(data), # TODO lots...
                        ]
                m.d.sync += [
                        self.dmactive.eq(req.dmactive),
                        self.ndmreset.eq(req.ndmreset),
                        self.hartsel.eq(Cat(req.hartsello, req.hartselhi))
                        ]
                with m.If(req.haltreq):
                    with Transaction().body(m):
                        self.halt(m)
                        m.d.sync += self.core_state.eq(CoreState.HALTED) # TODO for real
                        m.next = "RESP_WAITING"
                with m.Else():
                    m.next = "RESP_WAITING"
            with m.Case(0x11): # dmstatus
                m.d.av_comb += rsp_op.eq(2)
                m.next = "RESP_WAITING"
            with m.Case(0x16): # abstractcs
                m.d.av_comb += rsp_op.eq(0)
                req = Signal(self.abstractcs_layout)
                m.d.av_comb += [ # TODO anything else
                                req.eq(data)
                        ]
                with m.If(req.cmderr): # TODO proper value
                    m.d.sync += self.abstract_cmderr.eq(0)
                m.next = "RESP_WAITING"
            with m.Case(0x17): # command
                with m.If(self.abstract_busy):
                    m.d.av_comb += rsp_op.eq(2)
                with m.Else():
                    m.d.sync += self.abstract_busy.eq(1)
                    m.d.av_comb += rsp_op.eq(0)
                    req = Signal(self.command_layout)
                    m.d.av_comb += [
                            req.eq(data),
                            self.abstract_control.eq(req.control),
                            self.abstract_type.eq(req.cmdtype)
                            ]
                m.next = "RESP_WAITING"
            with m.Case(*range(0x20,0x30)): #progbuf
                write_pending = Signal(2) # TODO dedup some of these
                with m.If(write_pending == 0):
                    with Transaction().body(m):
                        self.bus.request_write(m, addr=(address - 0x20)*4 + 0x1000, data=data, sel=0xf)
                        m.d.sync += write_pending.eq(1)
                with m.Elif(write_pending == 1):
                    with Transaction().body(m):
                        fetched = self.bus.get_write_response(m) # TODO err
                        m.d.sync += write_pending.eq(2)
                with m.Elif(write_pending == 2):
                    with Transaction().body(m):
                        self.halt(m) # TODO don't need this one
                        self.flush_icache(m)
                        m.d.av_comb += rsp_op.eq(0)
                        m.d.sync += write_pending.eq(0)
                        m.next = "RESP_WAITING"

            with m.Default():
                m.d.av_comb += rsp_op.eq(0)
                m.next = "RESP_WAITING"

    def elaborate(self, platform):
        m = TModule()

        address = Signal(32) # TODO size
        data = Signal(32)
        op = Signal(2)

        rsp_data = Signal(32)
        rsp_op = Signal(2)


        # TODO this probably has more wait states than necessary, but I'd rather get it right first!
        with m.FSM():
            with m.State("REQ_READY"):
                m.next = "REQ_WAITING"
                m.d.sync += self.dmi.req_ready.eq(1)

            with m.State("REQ_WAITING"):
                with m.If(self.dmi.req_valid):
                    m.next = "REQ_PROCESSING"
                    m.d.sync += [
                            address.eq(self.dmi.req_address),
                            data.eq(self.dmi.req_data),
                            op.eq(self.dmi.req_op)
                            ]

            with m.State("REQ_PROCESSING"):
                m.d.sync += self.dmi.req_ready.eq(0)
                with m.If(self.dmi.req_op == 0): # NOP
                    m.next = "RESP_WAITING"
                    m.d.av_comb += rsp_op.eq(0)
                    m.d.sync += rsp_data.eq(0)
                with m.Elif(self.dmi.req_op == 1):
                    self.read(m, address, rsp_op, rsp_data)
                with m.Elif(self.dmi.req_op == 2):
                    m.d.sync += rsp_data.eq(0)
                    with m.If(self.dmactive.bool() | ((address == 0x10).bool() & (data == 1).bool())):
                        self.write(m, address, data, rsp_op)
                    with m.Else():
                        m.next = "RESP_WAITING"

            with m.State("RESP_WAITING"):
                with m.If(self.dmi.rsp_ready):
                    m.next = "RESP"

            with m.State("RESP"):
                with m.If(~self.dmi.rsp_ready):
                    m.next = "RESP_POST"
                m.d.sync += [
                        self.dmi.rsp_valid.eq(1),
                        self.dmi.rsp_data.eq(rsp_data),
                        self.dmi.rsp_op.eq(rsp_op)
                        ]
            with m.State("RESP_POST"):
                m.next = "REQ_READY"
                m.d.sync += self.dmi.rsp_valid.eq(0)

        with m.If(self.abstract_busy), m.Switch(self.abstract_type):
            with m.Case(0): # Access Register
                arreq = Signal(self.access_register_layout)
                m.d.av_comb += [
                        arreq.eq(data),
                        ]
                with m.If(arreq.regno >= 0x1000), m.FSM():
                    with m.State("Submit"), Transaction().body(m): # TODO write, transfer, err on postinc, sizes
                        self.rf_read_req[0](m, arreq.regno - 0x1000) # TODO lol proper address
                        m.next = "Read"
                    with m.State("Read"), Transaction().body(m):
                        reg_value = self.rf_read_resp[0](m, arreq.regno*4 - 0x1000)
                        m.d.sync += [
                                self.abstract_data[0].eq(reg_value.reg_val),
                                self.abstract_busy.eq(0),
                                self.abstract_cmderr.eq(0)
                                ]
                        with m.If(arreq.postexec):
                            m.next = "Flush"
                        with m.Else():
                            m.next = "Submit"
                    with m.State("Flush"), Transaction().body(m):
                        self.flush_icache(m)
                        m.next = "Exec"
                    with m.State("Exec"), Transaction().body(m):
                        self.exec(m, pc=0x1000) # TODO proper pc lol
                        m.next = "Submit"
                with m.Else():
                    m.d.sync += [
                            self.abstract_data[0].eq(0),
                            self.abstract_cmderr.eq(2),
                            self.abstract_busy.eq(0)
                            ]

            with m.Case(1), Transaction().body(m): # Quick Access
                self.halt(m)
                m.d.sync += self.core_state.eq(CoreState.HALTED)
                self.abstract_busy.eq(0)

            with m.Case(2), m.FSM(): # Access Memory
                with m.State("Submit"), Transaction().body(m):
                    self.bus.request_read(m, addr=self.abstract_data[1], sel=0xf)
                    m.next = "Read"
                with m.State("Read"), Transaction().body(m):
                    fetched = self.bus.get_write_response(m)
                    m.d.sync += [
                            self.abstract_data[0].eq(fetched),
                            self.abstract_busy.eq(0),
                            self.abstract_cmderr.eq(0)
                            ]
                    m.next = "Submit"

        # m.d.sync.reset += self.ndmreset lol how does reset work in coreblocks

        return m
