from decimal import Decimal

from common.model_pricing.decimals import per_million_to_per_token, to_rate_str


def test_trailing_zeros_do_not_change_the_string():
    aws = per_million_to_per_token("2.2000000000")
    litellm = per_million_to_per_token(2.2)
    assert to_rate_str(aws) == to_rate_str(litellm) == "0.0000022"


def test_fixed_point_never_scientific():
    assert to_rate_str(per_million_to_per_token("0.20")) == "0.0000002"
    assert to_rate_str(Decimal("10")) == "10"
    assert to_rate_str(Decimal("0")) == "0"
