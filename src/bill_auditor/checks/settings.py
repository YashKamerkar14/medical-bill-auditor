"""Settings shared by the checks and the test-case generator.

The generator and the checks must agree, or the answer key and the system
would disagree by construction; keeping each value in one place prevents that.
"""

# A self-pay line is reported as a price BENCHMARK (never an error) when its
# stated UNIT PRICE is at least this many times the Medicare amount. The unit
# price, not charge / units, is compared, so a line with an arithmetic error is
# not also reported as overpriced. Clean generated bills are priced at
# 1.3-2.5x Medicare, typical of real billing.
PRICE_BENCHMARK_RATIO = 3.0

# The price check runs on self-pay bills only: insured bills already have the
# insurer's allowed amount on the EOB.
PRICE_CHECK_SELF_PAY_ONLY = True
