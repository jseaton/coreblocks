from amaranth import *
from amaranth.lib.data import StructLayout
from amaranth.lib.wiring import Component, In, Out

from coreblocks.params.genparams import GenParams
from coreblocks.interface.layouts import *

from transactron.core import Transaction, TModule
from transactron.utils.transactron_helpers import make_layout
from transactron.lib.simultaneous import condition
from transactron.utils.amaranth_ext.component_interface import ComponentInterface, CIn, COut
from transactron import *

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

    def __init__(self, gen_params: GenParams):
        super().__init__({"dmi": Out(DebugModuleInterface().signature)})

        self.dmactive = Signal()
        self.ndmreset = Signal()

        self.abstract_type = Signal(8)
        self.abstract_control = Signal(24)

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

        self.command_layout = StructLayout({
            "control" : len(self.abstract_control),
            "cmdtype" : len(self.abstract_type)
            })

    def read(self, m, address, rsp_op, rsp_data):
        with m.Switch(address):
            with m.Case(0x10): # dmcontrol
                m.d.av_comb += rsp_op.eq(0)
                resp = Signal(self.dmcontrol_layout)
                m.d.av_comb += [
                        resp.dmactive.eq(self.dmactive),
                        resp.ndmreset.eq(self.ndmreset)
                        ]
                m.d.sync += rsp_data.eq(resp)
            with m.Case(0x11): # dmstatus
                m.d.av_comb += rsp_op.eq(0)
                resp = Signal(self.dmstatus_layout)
                m.d.av_comb += [
                        resp.version.eq(3),
                        resp.authenticated.eq(1)
                        ]
                m.d.sync += rsp_data.eq(resp)
            with m.Default():
                m.d.av_comb += rsp_op.eq(0)
                m.d.sync += rsp_data.eq(0)

    def write(self, m, address, data, rsp_op):
        with m.Switch(address):
            with m.Case(0x10): # dmcontrol
                m.d.av_comb += rsp_op.eq(0)
                req = Signal(self.dmcontrol_layout)
                m.d.av_comb += [
                        req.eq(data), # TODO lots...
                        ]
                m.d.sync += [
                        self.dmactive.eq(req.dmactive),
                        self.ndmreset.eq(req.ndmreset)
                        ]
            with m.Case(0x11): # dmstatus
                m.d.av_comb += rsp_op.eq(2)
            with m.Case(0x17): # command
                m.d.av_comb += rsp_op.eq(0) # TODO busy
                req = Signal(self.command_layout)
                m.d.av_comb += [
                        req.eq(data),
                        self.abstract_control.eq(req.control),
                        self.abstract_type.eq(req.cmdtype)
                        ]
            with m.Case(*range(0x20,0x30)): #progbuf
                m.d.av_comb += rsp_op.eq(0) # TODO busy stuff
                m.d.av_comb += self.progbuf[address - 0x20].eq(data)
            with m.Default():
                m.d.av_comb += rsp_op.eq(0)

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
                    m.next = "RESP_WAITING"
                    m.d.sync += rsp_data.eq(0)
                    with m.If(self.dmactive | address == 0x10):
                        self.write(m, address, data, rsp_op)

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

        # m.d.sync.reset += self.ndmreset lol how does reset work in coreblocks

        return m
