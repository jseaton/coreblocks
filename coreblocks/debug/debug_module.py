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

from coreblocks.interface.keys import CSRInstancesKey

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

    halt: Required[Method]
    """ Stop the core for debug mode or something idk """

    rf_read_req: Required[Methods]

    rf_read_resp: Required[Methods]

    def __init__(self, gen_params: GenParams):
        super().__init__({"dmi": Out(DebugModuleInterface().signature)})

        self.dm = DependencyContext.get()

        self.dmactive = Signal()
        self.ndmreset = Signal()

        self.core_state = Signal(CoreState, init=CoreState.RUNNING)

        self.hartsel = Signal(1)


        self.abstract_type = Signal(8)
        self.abstract_control = Signal(24)

        self.abstract_busy = Signal()

        self.abstract_data = Signal(32) # yes, just the one hopefully

        self.progbuf = Array([Signal(32)]*16)

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

        self.halt = Method()

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
                m.d.sync += rsp_data.eq(self.abstract_data)
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
            with m.Case(0x16): # abstractcs
                resp = Signal(self.abstractcs_layout)
                m.d.av_comb += [
                        resp.datacount.eq(1),
                        resp.busy.eq(self.abstract_busy),
                        resp.progbufsize.eq(16)
                        ]
                m.d.sync += rsp_data.eq(resp)

            with m.Default():
                m.d.av_comb += rsp_op.eq(0)
                m.d.sync += rsp_data.eq(0)

    def write(self, m, address, data, rsp_op):
        with m.Switch(address):
            with m.Case(0x4): # data0
                m.d.av_comb += rsp_op.eq(0)
                m.d.sync += self.abstract_data.eq(data)
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
                m.d.av_comb += rsp_op.eq(0) # TODO busy stuff
                m.d.av_comb += self.progbuf[address - 0x20].eq(data)
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
                    m.next = "RESP_WAITING"
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

        csr = self.dm.get_dependency(CSRInstancesKey())

        with m.If(self.abstract_busy):
            with m.Switch(self.abstract_type):
                with m.Case(0): # Access Register
                    arreq = Signal(self.access_register_layout)
                    m.d.av_comb += [
                            arreq.eq(data),
                            ]
                    with m.If(arreq.regno >= 0x1000):
                        with m.FSM():
                            with m.State("Submit"):
                                with Transaction().body(m):
                                    self.rf_read_req[0](m, arreq.regno - 0x1000)
                                    m.next = "Read"
                            with m.State("Read"):
                                with Transaction().body(m):
                                    reg_value = self.rf_read_resp[0](m, arreq.regno - 0x1000)
                                    m.d.sync += [
                                            self.abstract_data.eq(reg_value.reg_val),
                                            self.abstract_busy.eq(0)
                                            ]
                                    m.next = "Submit"
                    with m.Else():
                        with m.If(arreq.regno == 0x301):
                                with Transaction().body(m):
                                    reg_value = csr.m_mode.misa.read(m)
                                    m.d.sync += [
                                            self.abstract_data.eq(reg_value.data),
                                            self.abstract_busy.eq(0)
                                            ]

                with m.Case(1): # Quick Access
                    with Transaction().body(m):
                        self.halt(m)
                        m.d.sync += self.core_state.eq(CoreState.HALTED)
                        self.abstract_busy.eq(0)


        # m.d.sync.reset += self.ndmreset lol how does reset work in coreblocks

        return m
