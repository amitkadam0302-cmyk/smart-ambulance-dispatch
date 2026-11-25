class Problem(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def require(condition, message: str, code: str = "state_conflict", status: int = 409):
    if not condition:
        raise Problem(status, code, message)
