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


class JTAGDebugInterface(ComponentInterface):
    def __init__(self):
        self.tck = CIn() # TODO magic clock?
        self.tms = CIn(32)
        self.tdi = CIn(32)
        self.tdo = COut(32)

class DebugJTAGTAP(Component):
    jtag: JTAGDebugInterface

    def __init__(self, gen_params: GenParams):
        super().__init__({"jtag": Out(JTAGDebugInterface().signature)})

    def elaborate(self, platform):
        m = TModule()

        self.jtag.tck = Signal()
        self.jtag.tms = Signal(32)
        self.jtag.tdi = Signal(32)
        self.jtag.tdo = Signal(32)

        m.d.sync += self.jtag.tdo.eq(self.jtag.tdo + 1)

#        address = Signal(32) # TODO size
#        data = Signal(32)
#        op = Signal(2)
#
#        rsp_data = Signal(32)
#        rsp_op = Signal(2)
#
#        dtmcs = Signal(32)
#        dmi = Signal(32)
#
#        dmi_op = Signal(2)
#
#        dtmcs_layout = StructLayout({
#            "version":   4,
#            "abits": 6,
#            "dmistat":  2,
#            "idle": 3,
#            "0" : 1,
#            "dmireset" : 1,
#            "dtmhardreset" : 1,
#            "errinfo" : 3
#        })
#
#        # TODO this probably has more wait states than necessary, but I'd rather get it right first!
#        with m.FSM():
#            with m.State("REQ_READY"):
#                m.next = "REQ_WAITING"
#                m.d.sync += self.req_ready.eq(1)
#
#            with m.State("REQ_WAITING"):
#                with m.If(self.req_valid):
#                    m.next = "REQ_PROCESSING"
#                    m.d.sync += [
#                            address.eq(self.req_address),
#                            data.eq(self.req_data),
#                            op.eq(self.req_op)
#                            ]
#            # TODO yeah we really don't need this state...
#            with m.State("REQ_PROCESSING"):
#                m.d.sync += self.req_ready.eq(0)
#                with m.If(self.req_op == 0): # NOP
#                    m.next = "RESP_WAITING"
#                    m.d.av_comb += rsp_op.eq(0)
#                    m.d.av_comb += rsp_data.eq(0)
#                with m.Elif(self.req_op == 1):
#                    m.next = "RESP_WAITING"
#                    with m.Switch(address): # TODO introduce IR
#                        with m.Case(0x0, 0x12, 0x13, 0x14, 0x15, 0x15, 0x17, 0x1f): # bypass
#                            m.d.av_comb += rsp_op.eq(0)
#                            m.d.av_comb += rsp_data.eq(0)
#                        with m.Case(0x1): # idcode
#                            m.d.av_comb += rsp_op.eq(0)
#                            m.d.av_comb += rsp_data.eq(0x12345)
#                        with m.Case(0x10): # dtmcs
#                            m.d.av_comb += rsp_op.eq(0)
#                            resp = Signal(dtmcs_layout)
#                            m.d.av_comb += [
#                                    resp.version.eq(1),
#                                    resp.abits.eq(32),
#                                    resp.dmistat.eq(dmi_op),
#                                    resp.idle.eq(10)
#                                    ]
#                            m.d.av_comb += rsp_data.eq(resp)
#                        with m.Case(0x11): # dtmcs
#                            m.d.av_comb += rsp_op.eq(0)
#                            m.d.av_comb += rsp_data.eq(dmi)
#                with m.Elif(self.req_op == 2):
#                    m.next = "RESP_WAITING"
#                    with m.Switch(address):
#                        with m.Case(0x0, 0x1, 0x12, 0x13, 0x14, 0x15, 0x15, 0x17, 0x1f): # bypass, idcode
#                            m.d.av_comb += rsp_op.eq(2)
#                        with m.Case(0x10): # dtmcs
#                            m.d.av_comb += rsp_op.eq(0)
#                            req = Signal(dtmcs_layout)
#                            m.d.av_comb += req.eq(data)
#                            with m.If(req.dmireset): # TODO
#                                pass
#                            with m.If(req.dtmhardreset): # TODO
#                                pass
#                        with m.Case(0x11): # dtmcs
#                            m.d.av_comb += rsp_op.eq(0)
#                            m.d.av_comb += rsp_data.eq(dmi)
#
#            with m.State("RESP_WAITING"):
#                with m.If(self.rsp_ready):
#                    m.next = "RESP"
#
#            with m.State("RESP"):
#                with m.If(~self.rsp_ready):
#                    m.next = "RESP_POST"
#                m.d.sync += [
#                        self.rsp_ready.eq(1),
#                        self.rsp_data.eq(rsp_data),
#                        self.rsp_op.eq(rsp_op)
#                        ]
#            with m.State("RESP_POST"):
#                m.next = "REQ_READY"
#                m.d.sync += self.rsp_ready.eq(0)

        return m
