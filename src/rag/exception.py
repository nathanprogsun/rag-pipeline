class RAGError(Exception):
    pass


class NoResultsError(RAGError):
    pass


class ConfigError(RAGError):
    pass


class RetrievalError(RAGError):
    pass
