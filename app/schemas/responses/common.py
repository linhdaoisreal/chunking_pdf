from pydantic import BaseModel


class ApiResponse[DataT](BaseModel):
    statusCode: int
    data: DataT


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorData(BaseModel):
    error: ErrorDetail
    request_id: str


class StatusData(BaseModel):
    status: str
