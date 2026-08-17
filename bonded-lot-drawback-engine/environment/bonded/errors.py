"""Named failures for the Bondwright ledger."""


class BondedError(Exception):
    """Base class for every ledger refusal."""


class UnknownHts(BondedError):
    pass


class UnknownLot(BondedError):
    pass


class DuplicateLot(BondedError):
    pass


class DuplicateRef(BondedError):
    pass


class InsufficientQuantity(BondedError):
    pass


class LotExpired(BondedError):
    pass


class UnitMismatch(BondedError):
    pass


class InvalidBom(BondedError):
    pass


class OverClaim(BondedError):
    pass


class DrawbackWindowClosed(BondedError):
    pass


class IllegalTransition(BondedError):
    pass
