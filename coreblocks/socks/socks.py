from amaranth import *
from amaranth.lib.wiring import Component, In, Out, connect, flipped

from coreblocks.arch.isa_consts import InterruptCauseNumber
from coreblocks.core import Core
from coreblocks.params import GenParams
from coreblocks.peripherals.wishbone import WishboneInterface, WishboneMuxer
from coreblocks.priv.traps.interrupt_controller import ISA_RESERVED_INTERRUPTS
from coreblocks.socks.clint import ClintPeriph
from coreblocks.socks.peripheral import bus_in_periph_range
from coreblocks.socks.plic import PlicPeriph
from coreblocks.debug.debug_jtag import JTAGDebugInterface

CLINT_BASE = 0xE1000000
PLIC_BASE = 0xE2000000

# This is a temporary wrapper solution to provide external memory-mapped components, required to run Linux.
# It is intended to be only usable with LiteX, that uses a simple core-facing interface and Wishbone bus
# (and is currenty the only supported integration).
# TODO: Replace this with a proper bus-agnostic (and multicore?) soultion at a later stage.


class Socks(Component):
    wb_instr: WishboneInterface
    wb_data: WishboneInterface

    interrupts: Signal
    """ Interrupts input signal
    If `with_plic` is set to True, then it's the input to the RISC-V Platform Level Interrupt Controller module.
    PLIC wires interrupts contexts 0 to MEI and 1 to SEI. Signal has width of `interrupt_custom_count +1`.
    Note that PLIC interrupt 0 is reserved and bit 0 is ignored.
    If `with_plic` is set to False, `interrupts` width is 16 (number of interrupts reserved by ISA) +
    `interrupt_custom_count` and interrupts are directly wired to Hart Local Interrupt Controller.
    In both cases MTI and MSI are ignored and provided from CLINT.
    """

    def __init__(self, core: Core, core_gen_params: GenParams, with_plic: bool = True, with_debug: bool = True):
        super().__init__(
            {
                "wb_instr": Out(WishboneInterface(core_gen_params.wb_params).signature),
                "wb_data": Out(WishboneInterface(core_gen_params.wb_params).signature),
                "interrupts": In(
                    (1 if with_plic else ISA_RESERVED_INTERRUPTS) + core_gen_params.interrupt_custom_count
                ),
                "debug": Out(JTAGDebugInterface().signature)
            }
        )

        self.clint = ClintPeriph(base_addr=CLINT_BASE, wb_params=core_gen_params.wb_params)
        if with_plic:
            self.plic = PlicPeriph(
                base_addr=PLIC_BASE,
                wb_params=core_gen_params.wb_params,
                interrupt_count=core_gen_params.interrupt_custom_count,
                context_count=2,
            )
        else:
            self.plic = None

        if with_debug:
            self.wb_debug = core.wb_slave_debug
        else:
            self.wb_debug = None

        self.core = core
        self.core_gen_params = core_gen_params

    def elaborate(self, platform):
        m = Module()

        muxer_ssel = Signal(3)
        imuxer_ssel = Signal(2)
        periph_muxer = WishboneMuxer(self.core_gen_params.wb_params, muxer_ssel)
        instr_muxer = WishboneMuxer(self.core_gen_params.wb_params, imuxer_ssel)

        connect(m, self.core.wb_instr, instr_muxer.master_wb)

        connect(m, self.core.wb_data, periph_muxer.master_wb)

        connect(m, self.core.debug, flipped(self.debug))
        connect(m, periph_muxer.slaves[0], flipped(self.wb_data))

        connect(m, periph_muxer.slaves[1], self.clint.bus)
        if self.plic:
            connect(m, periph_muxer.slaves[2], self.plic.bus)

        connect(m, instr_muxer.slaves[0], flipped(self.wb_instr))
        if self.wb_debug:
            connect(m, instr_muxer.slaves[1], self.wb_debug.bus)

        clint_addr = Signal()
        plic_addr = Signal()
        debug_addr = Signal()
        m.d.comb += clint_addr.eq(bus_in_periph_range(self.core.wb_data, self.clint))
        if self.plic:
            m.d.comb += plic_addr.eq(bus_in_periph_range(self.core.wb_data, self.plic))
        if self.wb_debug:
            m.d.comb += debug_addr.eq(bus_in_periph_range(self.core.wb_instr, self.wb_debug))
        m.d.comb += muxer_ssel.eq(Cat(~(clint_addr | plic_addr), clint_addr, plic_addr))
        m.d.comb += imuxer_ssel.eq(Cat(~(debug_addr), debug_addr))

        m.submodules.clint = self.clint
        if self.plic:
            m.submodules.plic = self.plic
        #if self.debug:
        #    m.submodules.debug = self.debug
        m.submodules.periph_muxer = periph_muxer
        m.submodules.instr_muxer = instr_muxer

        m.submodules.core = self.core

        if self.plic:
            m.d.comb += self.plic.interrupts.eq(self.interrupts)
            m.d.comb += self.core.interrupts[InterruptCauseNumber.MEI].eq(self.plic.eip[0])
            m.d.comb += self.core.interrupts[InterruptCauseNumber.SEI].eq(self.plic.eip[1])
        else:
            m.d.comb += self.core.interrupts[InterruptCauseNumber.MEI].eq(self.interrupts[InterruptCauseNumber.MEI])
            m.d.comb += self.core.interrupts[ISA_RESERVED_INTERRUPTS:].eq(self.interrupts[ISA_RESERVED_INTERRUPTS:])
        m.d.comb += self.core.interrupts[InterruptCauseNumber.MTI].eq(self.clint.mtip)
        m.d.comb += self.core.interrupts[InterruptCauseNumber.MSI].eq(self.clint.msip)

        return m
