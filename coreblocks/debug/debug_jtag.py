from amaranth import *
from amaranth.lib.data import StructLayout, View
from amaranth.lib.wiring import Component, In, Out

from coreblocks.params.genparams import GenParams
from coreblocks.interface.layouts import *

from transactron.core import Transaction, TModule
from transactron.utils.transactron_helpers import make_layout
from transactron.lib.simultaneous import condition
from transactron.utils.amaranth_ext.component_interface import ComponentInterface, CIn, COut
from transactron import *

from coreblocks.debug.debug_module import DebugModuleInterface, ABITS


class JTAGDebugInterface(ComponentInterface):
    def __init__(self):
        self.tck = CIn()
        self.tms = CIn()
        self.tdi = CIn()
        self.tdo = COut()

class DebugJTAGTAP(Component):
    jtag: JTAGDebugInterface
    dmi: DebugModuleInterface

    def __init__(self, gen_params: GenParams):
        super().__init__({
            "jtag": Out(JTAGDebugInterface().signature),
            "dmi": Out(DebugModuleInterface().signature)})

    def elaborate(self, platform):
        m = TModule()

        dtmcs_layout = StructLayout({
            "version": 4,
            "abits": 6,
            "dmistat":  2,
            "idle": 3,
            "0_0" : 1,
            "dmireset" : 1,
            "dtmhardreset" : 1,
            "errinfo" : 3,
            "0_1" : 11,
        })

        dmi_layout = StructLayout({
            "op" : 2,
            "data" : 32,
            "address" : ABITS
        })

        m.domains.jtag_pos = cd_jtag_pos = ClockDomain()
        m.domains.jtag_neg = cd_jtag_neg = ClockDomain()
        m.d.comb += cd_jtag_pos.clk.eq(self.jtag.tck)
        m.d.comb += cd_jtag_neg.clk.eq(~self.jtag.tck)

        ir = Signal(5)
        dr = Signal(2+32+ABITS)

        read = Signal()
        write = Signal()

        req_dmi_op = Signal(2)
        req_dmi_data = Signal(32)
        req_dmi_address = Signal(32)

        rsp_dmi_op = Signal(2)
        rsp_dmi_data = Signal(32)

        dmi_submit = Signal()

        wtf = Signal(4)

        reset_fsm = Signal()

        width = {
                0x0 : 1, 0x12 : 1, 0x13 : 1, 0x14 : 1, 0x15 : 1, 0x17 : 1, 0x1f : 1,
                0x1 : 32,
                0x10 : 32,
                0x11 : 33+ABITS
                }

        with m.FSM(domain="jtag_pos"):
            with m.State("Test-Logic-Reset"): # 0
                m.d.comb += wtf.eq(0)
                with m.If(~self.jtag.tms):
                    m.next = "Idle"
                m.d.jtag_pos += ir.eq(1)
            with m.State("Idle"): # 1
                m.d.comb += wtf.eq(1)
                m.d.sync += dmi_submit.eq(0)
                with m.If(self.jtag.tms):
                    m.next = "Select-DR-Scan"

            with m.State("Select-DR-Scan"): # 2
                m.d.comb += wtf.eq(2)
                m.d.sync += dmi_submit.eq(0)
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
                m.d.comb += write.eq(1)
                with m.If(reset_fsm):
                    m.next = "Test-Logic-Reset"
                with m.Elif(self.jtag.tms):
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

        # TODO clock domain crossing...
        with m.If(read), m.Switch(ir):
            with m.Case(0x0, 0x12, 0x13, 0x14, 0x15, 0x17, 0x1f): # bypass
                m.d.jtag_pos += dr.eq(0)
            with m.Case(0x1): # idcode
                m.d.jtag_pos += dr.eq(0x10003FFF)
            with m.Case(0x10): # dtmcs
                resp = Signal(dtmcs_layout)
                m.d.comb += [
                        resp.version.eq(1),
                        resp.abits.eq(ABITS),
                        resp.dmistat.eq(rsp_dmi_op),
                        resp.idle.eq(100)
                        ]
                m.d.jtag_pos += dr.eq(resp)
            with m.Case(0x11): # dmi
                resp = Signal(dmi_layout)
                m.d.comb += [
                        resp.op.eq(rsp_dmi_op),
                        resp.data.eq(rsp_dmi_data),
                        ]
                m.d.jtag_pos += dr.eq(resp)

        with m.If(write), m.Switch(ir):
            with m.Case(0x0, 0x1, 0x12, 0x13, 0x14, 0x15, 0x17, 0x1f): # bypass, idcode
                pass
            with m.Case(0x10): # dtmcs
                req = View(dtmcs_layout, dr[:32])
                with m.If(req.dmireset): # TODO reset fsm...
                    m.d.jtag_pos += [
                            dr.eq(0),
                            ir.eq(1)
                            ]
                    m.d.comb += reset_fsm.eq(1)
                with m.If(req.dtmhardreset): # TODO
                    pass
            with m.Case(0x11): # dmi
                req = View(dmi_layout, dr)
                m.d.sync += [
                        req_dmi_op.eq(req.op),
                        req_dmi_data.eq(req.data),
                        req_dmi_address.eq(req.address),
                        dmi_submit.eq(1)
                        ]

        # dmi
        with m.FSM():
            with m.State("Idle"):
                with m.If(dmi_submit & self.dmi.req_ready):
                    m.next = "Wait"

                    m.d.sync += [
                            self.dmi.req_op.eq(req_dmi_op),
                            self.dmi.req_data.eq(req_dmi_data),
                            self.dmi.req_address.eq(req_dmi_address),
                            self.dmi.req_valid.eq(1),
                            self.dmi.rsp_ready.eq(1)
                            ]

            with m.State("Wait"):
                m.d.sync += self.dmi.req_valid.eq(0)

                with m.If(self.dmi.rsp_valid):
                    m.next = "Wait-Submit-Done"

                    m.d.sync += [
                            rsp_dmi_op.eq(self.dmi.rsp_op),
                            rsp_dmi_data.eq(self.dmi.rsp_data),
                            self.dmi.rsp_ready.eq(0)
                            ]

            with m.State("Wait-Submit-Done"):
                with m.If(~dmi_submit):
                    m.next = "Idle"

        return m
