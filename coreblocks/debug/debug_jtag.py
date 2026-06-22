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
        self.tms = CIn()
        self.tdi = CIn()
        self.tdo = COut()

class DebugJTAGTAP(Component):
    jtag: JTAGDebugInterface

    def __init__(self, gen_params: GenParams):
        super().__init__({"jtag": Out(JTAGDebugInterface().signature)})

    def elaborate(self, platform):
        m = TModule()

        dtmcs_layout = StructLayout({
            "version": 4,
            "abits": 6,
            "dmistat":  2,
            "idle": 3,
            "0" : 1,
            "dmireset" : 1,
            "dtmhardreset" : 1,
            "errinfo" : 3
        })

        m.domains.jtag_pos = cd_jtag_pos = ClockDomain()
        m.domains.jtag_neg = cd_jtag_neg = ClockDomain()
        m.d.comb += cd_jtag_pos.clk.eq(self.jtag.tck)
        m.d.comb += cd_jtag_neg.clk.eq(~self.jtag.tck)

        ir = Signal(5)
        dr = Signal(33+32)

        read = Signal()
        write = Signal()

        dtmcs = Signal(32)
        dmi = Signal(32)

        dmi_op = Signal(2)

        wtf = Signal(3)

        width = {
                0x0 : 1, 0x12 : 1, 0x13 : 1, 0x14 : 1, 0x15 : 1, 0x15 : 1, 0x17 : 1, 0x1f : 1,
                0x1 : 32,
                0x10 : 32,
                0x11 : 33+32
                }

        with m.FSM(domain="jtag_pos"):
            with m.State("Test-Logic-Reset"): # 0
                m.d.comb += wtf.eq(0)
                with m.If(~self.jtag.tms):
                    m.next = "Idle"
                m.d.jtag_pos += ir.eq(1)
            with m.State("Idle"): # 1
                m.d.comb += wtf.eq(1)
                with m.If(self.jtag.tms):
                    m.next = "Select-DR-Scan"

            with m.State("Select-DR-Scan"): # 2
                m.d.comb += wtf.eq(2)
                with m.If(self.jtag.tms):
                    m.next = "Select-IR-Scan"
                with m.Else():
                    m.next = "Capture-DR"
            with m.State("Capture-DR"): # 3
                m.d.comb += wtf.eq(3)
                with m.If(self.jtag.tms):
                    m.next = "Exit1-DR"
                with m.Else():
                    m.next = "Shift-DR"
                m.d.comb += read.eq(1)
            with m.State("Shift-DR"): # 4
                m.d.comb += wtf.eq(4)
                m.d.comb += read.eq(0)
                with m.If(self.jtag.tms):
                    m.next = "Exit1-DR"
                with m.Else():
                    m.d.jtag_neg += self.jtag.tdo.eq(0)
                    for k,v in width.items(): # TODO is there a nicer way to do this?
                        with m.If(ir == k):
                            m.d.jtag_neg += self.jtag.tdo.eq(dr[0])
                            m.d.jtag_pos += dr.eq(Cat(dr[1:v], self.jtag.tdi))
            with m.State("Exit1-DR"): # 5
                m.d.comb += wtf.eq(5)
                with m.If(self.jtag.tms):
                    m.next = "Update-DR"
                with m.Else():
                    m.next = "Pause-DR"
            with m.State("Pause-DR"):
                m.d.comb += wtf.eq(6)
                with m.If(self.jtag.tms): # 6
                    m.next = "Exit2-DR"
            with m.State("Exit2-DR"): # 7
                m.d.comb += wtf.eq(7)
                with m.If(self.jtag.tms):
                    m.next = "Update-DR"
                with m.Else():
                    m.next = "Shift-DR"
            with m.State("Update-DR"): # 8
                m.d.comb += wtf.eq(8)
                # TODO write
                with m.If(self.jtag.tms):
                    m.next = "Select-DR-Scan"
                with m.Else():
                    m.next = "Idle"

            with m.State("Select-IR-Scan"): # 9
                m.d.comb += wtf.eq(9)
                with m.If(self.jtag.tms):
                    m.next = "Test-Logic-Reset"
                with m.Else():
                    m.next = "Capture-IR"
            with m.State("Capture-IR"): # a
                m.d.comb += wtf.eq(0xa)
                m.d.jtag_pos += ir.eq(1)
                with m.If(self.jtag.tms):
                    m.next = "Exit1-IR"
                with m.Else():
                    m.next = "Shift-IR"
            with m.State("Shift-IR"): # b
                m.d.comb += wtf.eq(0xb)
                with m.If(self.jtag.tms):
                    m.next = "Exit1-IR"
                with m.Else():
                    m.d.jtag_neg += self.jtag.tdo.eq(ir[0])
                    m.d.jtag_pos += ir.eq(Cat(ir[1:], self.jtag.tdi))
            with m.State("Exit1-IR"): # c
                m.d.comb += wtf.eq(0xc)
                with m.If(self.jtag.tms):
                    m.next = "Update-IR"
                with m.Else():
                    m.next = "Pause-IR"
            with m.State("Pause-IR"): # d
                m.d.comb += wtf.eq(0xd)
                with m.If(self.jtag.tms):
                    m.next = "Exit2-IR"
            with m.State("Exit2-IR"): # e
                m.d.comb += wtf.eq(0xe)
                with m.If(self.jtag.tms):
                    m.next = "Update-IR"
                with m.Else():
                    m.next = "Shift-IR"
            with m.State("Update-IR"): # f
                m.d.comb += wtf.eq(0xf)
                with m.If(self.jtag.tms):
                    m.next = "Select-DR-Scan"
                with m.Else():
                    m.next = "Idle"

        with m.If(read), m.Switch(ir):
            with m.Case(0x0, 0x12, 0x13, 0x14, 0x15, 0x15, 0x17, 0x1f): # bypass
                m.d.jtag_pos += dr.eq(0)
            with m.Case(0x1): # idcode
                m.d.jtag_pos += dr.eq(0x10003FFF)
            with m.Case(0x10): # dtmcs
                resp = Signal(dtmcs_layout) # TODO use View
                m.d.comb += [
                        resp.version.eq(1),
                        resp.abits.eq(32),
                        resp.dmistat.eq(dmi_op),
                        resp.idle.eq(10)
                        ]
                m.d.jtag_pos += dr.eq(resp)
            with m.Case(0x11): # dmi
                m.d.jtag_pos += dr.eq(dmi)

        with m.If(write), m.Switch(ir):
            with m.Case(0x0, 0x1, 0x12, 0x13, 0x14, 0x15, 0x15, 0x17, 0x1f): # bypass, idcode
                pass # m.d.jtag_neg += rsp_op.eq(2) TODO error?
            with m.Case(0x10): # dtmcs
                req = Signal(dtmcs_layout) # TODO use View
                m.d.jtag_neg += req.eq(dr)
                with m.If(req.dmireset): # TODO
                    pass
                with m.If(req.dtmhardreset): # TODO
                    pass
            with m.Case(0x11): # dtmcs
                m.d.jtag_neg += dmi.eq(dr)

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
