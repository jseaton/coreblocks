from amaranth import *
from amaranth.lib.wiring import Component, In, Out
from coreblocks.params.genparams import GenParams
from transactron.core import Transaction, TModule

class DebugModule(Component):
    req_ready: Out(1)
    req_valid: In(1)
    req_op: In(2)
    req_address: In(32) # TODO abits
    req_data: In(32) # TODO nope this is fine??

    rsp_ready: Out(1)
    rsp_op: Out(2)
    rsp_data: Out(32)


    def __init__(self, gen_params: GenParams):
        super().__init__()

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
                m.d.sync += self.req_ready.eq(1)

            with m.State("REQ_WAITING"):
                with m.If(self.req_valid):
                    m.next = "REQ_PROCESSING"
                    m.d.sync += [
                            address.eq(self.req_address),
                            data.eq(self.req_data),
                            op.eq(self.req_op)
                            ]
            # TODO yeah we really don't need this state...
            with m.State("REQ_PROCESSING"):
                m.next = "RESP_WAITING"
                with m.If(self.req_op == 0): # NOP
                    pass
                with m.If(self.req_op == 1):
                    m.d.sync += [
                            rsp_data.eq(0x1234),
                            rsp_op.eq(0)
                            ]
                with m.If(self.req_op == 2):
                    m.d.sync += rsp_op.eq(0)
                m.d.sync += self.req_ready.eq(0)

            with m.State("RESP_WAITING"):
                with m.If(self.rsp_ready):
                    m.next = "RESP"

            with m.State("RESP"):
                with m.If(~self.rsp_ready):
                    m.next = "RESP_POST"
                m.d.sync += [
                        self.rsp_ready.eq(1),
                        self.rsp_data.eq(rsp_data),
                        self.rsp_op.eq(rsp_op)
                        ]
            with m.State("RESP_POST"):
                m.next = "REQ_READY"
                m.d.sync += self.rsp_ready.eq(0)

        return m
