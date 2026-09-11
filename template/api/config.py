# --- password reset ---------------------------------------------------------
# A six-digit code mailed to the account's address. The two limits below are
# what make six digits enough: five wrong guesses burns the code, and it dies
# after an hour regardless. Nobody walks a million codes through either.
RESET_CODE_TTL_MINUTES = 60
RESET_CODE_MAX_ATTEMPTS = 5
# One mail per account per minute, so /forgot-password cannot be pointed at
# someone's inbox as a way of flooding it.
RESET_CODE_COOLDOWN_SECONDS = 60
