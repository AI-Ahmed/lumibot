from lumibot.entities import TradingFee, TradingSlippage


class TestTradingFee:
    def test_init(self):
        fee = TradingFee(flat_fee=5.2)
        assert fee.flat_fee == 5.2

    def test_slippage_init(self):
        slippage = TradingSlippage(amount=0.15)
        assert slippage.amount == 0.15
