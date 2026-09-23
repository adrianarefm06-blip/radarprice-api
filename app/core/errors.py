class DomainError(Exception):
    """Error de dominio con mensaje apto para el cliente."""


class ProductNotFoundError(DomainError):
    def __init__(self, sku: str) -> None:
        super().__init__(f"Producto no encontrado: {sku}")
        self.sku = sku


class SyncAlreadyRunningError(DomainError):
    def __init__(self) -> None:
        super().__init__("Ya hay una sincronización en curso")
