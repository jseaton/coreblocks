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
    FlushICacheKey,
    CSRInstancesKey,
    DebugInterruptKey
)
from coreblocks.arch import ExceptionCause

class CoreState(Enum):
    NONEXISTENT = auto()
    UNAVAIL = auto()
    HALTED = auto()
    RUNNING = auto()

ABITS = 16 # TODO make smaller...

class DebugModule(Component):
    raise_debug_interrupt: Required[Method]

    rf_read_req: Required[Methods]
    rf_read_resp: Required[Methods]
    rf_write: Required[Method]

    reg_get_rename: Required[Method]

    def __init__(self, gen_params: GenParams, progbuf: Memory):
        self.progbuf = progbuf
        self.progbuf_read = progbuf.read_port()
        self.progbuf_write = progbuf.write_port()

        self.dm = DependencyContext.get()

        self.gen_params = gen_params

        self.dmactive = Signal()
        self.ndmreset = Signal()

        self.core_state = Signal(CoreState, init=CoreState.RUNNING)

        self.debug_interrupt = Signal()

        self.allresumeack = Signal()

        self.hartsel = Signal(1)


        self.abstract_type = Signal(8)
        self.abstract_control = Signal(24)

        self.abstract_busy = Signal()
        self.abstract_cmderr = Signal(3)
        self.abstract_reg = Signal(32)

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

        # For halting
        self.raise_debug_interrupt = Method()

        # For progbuf
        self.flush_icache = self.dm.get_dependency(FlushICacheKey())

        # For resuming
        self.debug_resume = Method(i=layouts.backend_redirect)
        self.debug_guard = Method()
        self.leave_debug = Method()
        self.ftq_commit_ptr = Method(o=self.gen_params.get(FTQPtrLayout))

        # For register read/write
        # TODO just use one
        self.rf_read_req = Methods(2 * gen_params.frontend_superscalarity, i=gen_params.get(RFLayouts).rf_read_in)
        self.rf_read_resp = Methods(
            2 * gen_params.frontend_superscalarity,
            i=gen_params.get(RFLayouts).rf_read_in,
            o=gen_params.get(RFLayouts).rf_read_out,
        )
        self.rf_write = Method(i=gen_params.get(RFLayouts).rf_write)

        self.reg_get_rename = Method(i=gen_params.get(RATLayouts).get_reg_in, o=gen_params.get(RATLayouts).get_reg_out)

        self.clear_debug_interrupt = Method()
        self.dm.add_dependency(DebugInterruptKey(), self.debug_interrupt)

        layouts = gen_params.get(DebugLayouts)
        self.read_req = Method(i=layouts.read_req_in, o=layouts.read_req_out)
        self.write_req = Method(i=layouts.write_req_in, o=layouts.write_req_out)


    def elaborate(self, platform):
        m = TModule()

        # TODO make self
        csr_instances = self.dm.get_dependency(CSRInstancesKey())

        with m.If(self.abstract_busy), m.Switch(self.abstract_type):
            with m.Case(0): # Access Register
                arreq = Signal(self.access_register_layout)
                m.d.av_comb += [
                    arreq.eq(self.abstract_reg),
                ]
                with m.If((arreq.aarsize != 2).bool() | arreq.aarpostincrement): # 32-bit, post increment
                    m.d.sync += [
                            self.abstract_busy.eq(0),
                            self.abstract_cmderr.eq(2) # not supported TODO add enum
                            ]
                with m.Elif((arreq.regno >= 0x1000).bool() & ~arreq.write), m.FSM():
                    with m.State("Submit"), Transaction().body(m): # TODO transfer
                        r = self.reg_get_rename(m, rl=arreq.regno - 0x1000)
                        self.rf_read_req[0](m, reg_id=r.reg_id) # TODO lol proper address
                        m.next = "Read"
                    with m.State("Read"), Transaction().body(m):
                        reg_value = self.rf_read_resp[0](m, reg_id=arreq.regno*4 - 0x1000)
                        m.d.sync += self.abstract_data[0].eq(reg_value.reg_val)
                        with m.If(arreq.postexec):
                            m.next = "Flush"
                        with m.Else():
                            m.next = "Done"
                    with m.State("Flush"), Transaction().body(m):
                        self.flush_icache(m)
                        m.next = "Exec"
                    with m.State("Exec"), Transaction().body(m):
                        ftq_ptr = self.ftq_commit_ptr(m)
                        self.debug_resume(m, ftq_ptr=ftq_ptr, pc=0x02002000) # TODO proper pc lol
                        m.next = "Wait"
                    with m.State("Wait"), Transaction().body(m):
                        self.debug_guard(m)
                        reason = csr_instances.m_mode.dcsr_cause.read(m)
                        m.next = "Done"
                    with m.State("Done"):
                        m.d.sync += [
                                self.abstract_busy.eq(0),
                                self.abstract_cmderr.eq(0)
                                ]
                        with m.If(arreq.postexec & (reason.data != ExceptionCause.BREAKPOINT)):
                            m.d.sync += self.abstract_cmderr.eq(3)
                        m.next = "Submit"
                with m.Elif((arreq.regno >= 0x1000).bool() & arreq.write), m.FSM():
                    with m.State("Submit"), Transaction().body(m): # TODO transfer, merge this with write
                        r = self.reg_get_rename(m, rl=arreq.regno - 0x1000)
                        self.rf_write(m, reg_id=r.reg_id, reg_val=self.abstract_data[0])
                        m.next = "Flush"
                    with m.State("Flush"), Transaction().body(m):
                        self.flush_icache(m)
                        m.next = "Exec"
                    with m.State("Exec"), Transaction().body(m):
                        ftq_ptr = self.ftq_commit_ptr(m)
                        self.debug_resume(m, ftq_ptr=ftq_ptr, pc=0x02002000) # TODO proper pc lol
                        m.next = "Wait"
                    with m.State("Wait"), Transaction().body(m):
                        self.debug_guard(m)
                        reason = csr_instances.m_mode.dcsr_cause.read(m)
                        m.next = "Done"
                    with m.State("Done"):
                        m.d.sync += [
                                self.abstract_busy.eq(0),
                                self.abstract_cmderr.eq(0)
                                ]
                        with m.If(arreq.postexec & (reason.data != ExceptionCause.BREAKPOINT)):
                            m.d.sync += self.abstract_cmderr.eq(3)
                        m.next = "Submit"
                with m.Else():
                    m.d.sync += [
                            self.abstract_data[0].eq(0),
                            self.abstract_cmderr.eq(2),
                            self.abstract_busy.eq(0)
                            ]

            with m.Default():
                m.d.sync += [
                        self.abstract_busy.eq(0),
                        self.abstract_cmderr.eq(2)
                        ]

        # m.d.sync.reset += self.ndmreset lol how does reset work in coreblocks
        #

        read_pending = Signal()

        @def_method(m, self.read_req)
        def _(address):
            read_data = Signal(32)
            op = Signal(2)
            ready = Signal(1)
            m.d.comb += [
                ready.eq(1),
                read_data.eq(address)
            ]
            with m.Switch(address):
                with m.Case(0x4): # data0
                    m.d.comb += [read_data.eq(self.abstract_data[0])]
                with m.Case(0x5): # data1
                    m.d.comb += [read_data.eq(self.abstract_data[1])]
                with m.Case(0x10): # dmcontrol
                    resp = Signal(self.dmcontrol_layout) # TODO I'm sure there's a better way...
                    m.d.comb += [
                            resp.dmactive.eq(self.dmactive),
                            resp.ndmreset.eq(self.ndmreset),
                            resp.hartsello.eq(self.hartsel[:10]),
                            resp.hartselhi.eq(self.hartsel[10:])
                            ]
                    m.d.comb += [read_data.eq(resp)]
                with m.Case(0x11): # dmstatus
                    resp = Signal(self.dmstatus_layout)
                    m.d.comb += [
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
                            resp.anyresumeack.eq(self.allresumeack),
                            resp.allresumeack.eq(self.allresumeack),
                            ]
                    m.d.comb += [read_data.eq(resp)]
                with m.Case(0x16): # abstractcs
                    resp = Signal(self.abstractcs_layout)
                    m.d.comb += [
                            resp.datacount.eq(2),
                            resp.cmderr.eq(self.abstract_cmderr),
                            resp.busy.eq(self.abstract_busy),
                            resp.progbufsize.eq(16)
                            ]
                    m.d.comb += [read_data.eq(resp)]
                with m.Case(*range(0x20, 0x30)):
                        m.d.comb += [
                            self.progbuf_read.addr.eq(address - 0x20),
                            read_data.eq(self.progbuf_read.data)
                        ]

                with m.Default():
                    pass

            return {"data": read_data, "op": op, "ready": ready}

        csr_instances = self.dm.get_dependency(CSRInstancesKey())

        write_pending = Signal(2) # TODO dedup some of these

        #, ready=(self.dmactive | ((address == 0x10) & (data == 1)))) TODO
        @def_method(m, self.write_req)
        def _(address, write_data):
            op = Signal(2)
            ready = Signal(1)
            m.d.comb += [
                op.eq(2),
                ready.eq(1),
            ]
            with m.Switch(address):
                with m.Case(0x4): # data0
                    m.d.sync += self.abstract_data[0].eq(write_data)
                    m.d.comb += op.eq(0)
                with m.Case(0x5): # data1
                    m.d.sync += self.abstract_data[1].eq(write_data)
                    m.d.comb += op.eq(0)
                with m.Case(0x10): # dmcontrol
                    req = Signal(self.dmcontrol_layout)
                    m.d.comb += [
                            req.eq(write_data), # TODO lots...
                            ]
                    m.d.sync += [
                            self.dmactive.eq(req.dmactive),
                            self.ndmreset.eq(req.ndmreset),
                            self.hartsel.eq(Cat(req.hartsello, req.hartselhi))
                            ]
                    with m.If(req.haltreq):
                        m.d.sync += [
                                self.debug_interrupt.eq(1),
                                self.core_state.eq(CoreState.HALTED),
                                self.allresumeack.eq(0)
                                ]
                        m.d.comb += op.eq(0)
                    with m.Elif(req.resumereq): # TODO debug_stall first
                        #with Transaction().body(m):
                        self.debug_interrupt.eq(0),
                        self.leave_debug(m)
                        pc = csr_instances.m_mode.dpc.read(m)
                        ftq_ptr = self.ftq_commit_ptr(m)
                        self.debug_resume(m, ftq_ptr=ftq_ptr, pc=pc.data)
                        m.d.sync += [
                                self.core_state.eq(CoreState.RUNNING),
                                self.allresumeack.eq(1)
                                ]
                        m.d.comb += op.eq(0)
                    with m.Else():
                        m.d.comb += op.eq(0)
                with m.Case(0x11): # dmstatus
                    m.d.comb += op.eq(2)
                with m.Case(0x16): # abstractcs
                    req = Signal(self.abstractcs_layout)
                    m.d.comb += [ # TODO anything else
                                    req.eq(write_data)
                                    ]
                    with m.If(req.cmderr): # TODO proper value
                        m.d.sync += self.abstract_cmderr.eq(0)
                    m.d.comb += op.eq(0)
                with m.Case(0x17): # command
                    with m.If(self.abstract_busy):
                        m.d.sync += self.abstract_cmderr.eq(3)
                        m.d.comb += op.eq(2)
                    with m.Else():
                        m.d.sync += [
                                self.abstract_busy.eq(1),
                                self.abstract_reg.eq(write_data)
                        ]
                                
                        req = Signal(self.command_layout)
                        m.d.comb += [
                            req.eq(write_data),
                        ]
                        m.d.sync += [
                            self.abstract_control.eq(req.control),
                            self.abstract_type.eq(req.cmdtype)
                        ]
                        m.d.comb += op.eq(0)
                with m.Case(*range(0x20, 0x30)): #progbuf
                    self.flush_icache(m) # TODO we shouldn't be icached but we uh, are
                    m.d.comb += [
                        self.progbuf_write.addr.eq(address - 0x20),
                        self.progbuf_write.data.eq(write_data),
                        self.progbuf_write.en.eq(1)
                    ]

            return {"op": op, "ready": ready}

        @def_method(m, self.clear_debug_interrupt, nonexclusive=True)
        def _():
            m.d.sync += self.debug_interrupt.eq(0)

        return m
