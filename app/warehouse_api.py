"""Authenticated routes for the local warehouse; authentication lives in main.py."""
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .warehouse import WarehouseError


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateLocations(Body):
    rack_name: str = Field(min_length=1, max_length=80)
    shelf_count: int = Field(default=1, ge=1, le=100)
    places_per_shelf: int = Field(default=0, ge=0, le=100)
    kind: str = "shelf"
    name: str | None = Field(default=None, max_length=80)


class LocationId(Body):
    id: str = Field(min_length=1, max_length=100)


class RenameRack(Body):
    rack_name: str = Field(min_length=1, max_length=80)
    new_name: str = Field(min_length=1, max_length=80)


class UpdateLocation(LocationId):
    name: str = Field(min_length=1, max_length=80)
    rack: str | None = Field(default=None, max_length=80)
    expected_version: int | None = Field(default=None, ge=1)


class RegisterParcel(Body):
    number: str = Field(min_length=1, max_length=100)
    sender_name: str = Field(default="", max_length=500)


class MoveParcel(Body):
    number: str = Field(min_length=1, max_length=100)
    location_id: str | None = Field(default=None, max_length=100)
    expected_version: int = Field(ge=1)
    operation_id: str = Field(min_length=8, max_length=128)


class VersionedParcel(Body):
    number: str = Field(min_length=1, max_length=100)
    version: int = Field(ge=1)


class MoveLocation(Body):
    source_id: str = Field(min_length=1, max_length=100)
    target_id: str = Field(min_length=1, max_length=100)
    parcels: list[VersionedParcel] = Field(min_length=1, max_length=2000)
    operation_id: str = Field(min_length=8, max_length=128)


class Undo(Body):
    event_id: int = Field(ge=1)
    expected_version: int = Field(ge=1)
    operation_id: str = Field(min_length=8, max_length=128)


class Inventory(Body):
    location_id: str = Field(min_length=1, max_length=100)
    numbers: list[str] = Field(max_length=2000)


class SettingsBody(Body):
    courier_warning: bool | None = None
    photos: bool | None = None
    return_staging: bool | None = None


class Photo(Body):
    number: str = Field(min_length=1, max_length=100)
    image: str = Field(max_length=2_100_000)


class Stage(Body):
    parcels: list[VersionedParcel] = Field(min_length=1, max_length=2000)
    staged: bool = True


def register_warehouse_routes(app, warehouse, session_for):
    def actor(request):
        return session_for(request).get("actor", "Obsluha")

    @app.exception_handler(WarehouseError)
    async def warehouse_error(request, exc):
        return JSONResponse({"detail": exc.message}, status_code=exc.status)

    @app.get("/api/warehouse")
    async def snapshot():
        return warehouse.snapshot()

    @app.post("/api/locations/create")
    async def create_locations(body: CreateLocations):
        return {"locations": warehouse.create_locations(**body.model_dump())}

    @app.post("/api/locations/update")
    async def update_location(body: UpdateLocation):
        return {"location": warehouse.update_location(body.id, body.name, body.rack, body.expected_version)}

    @app.post("/api/locations/delete")
    async def delete_location(body: LocationId):
        return warehouse.delete_location(body.id)

    @app.post("/api/racks/rename")
    async def rename_rack(body: RenameRack):
        return {"locations": warehouse.rename_rack(body.rack_name, body.new_name)}

    @app.post("/api/parcels/register")
    async def register_parcel(request: Request, body: RegisterParcel):
        return {"parcel": warehouse.register_parcel(body.number, body.sender_name, actor(request))}

    @app.post("/api/parcels/move")
    async def move_parcel(request: Request, body: MoveParcel):
        return warehouse.move_parcel(**body.model_dump(), actor=actor(request))

    @app.post("/api/locations/move")
    async def move_location(request: Request, body: MoveLocation):
        return warehouse.move_location(**body.model_dump(), actor=actor(request))

    @app.get("/api/history")
    async def history(number: str):
        return {"events": warehouse.history(number)}

    @app.post("/api/moves/undo")
    async def undo(request: Request, body: Undo):
        return warehouse.undo_move(**body.model_dump(), actor=actor(request))

    @app.post("/api/inventory/check")
    async def inventory(body: Inventory):
        return warehouse.check_inventory(**body.model_dump())

    @app.post("/api/settings")
    async def settings(body: SettingsBody):
        return {"settings": warehouse.update_settings(body.model_dump(exclude_none=True))}

    @app.post("/api/parcels/photo")
    async def photo(request: Request, body: Photo):
        return {"parcel": warehouse.store_photo(body.number, body.image, actor(request))}

    @app.get("/api/parcels/{number}/photo")
    async def get_photo(number: str):
        return FileResponse(warehouse.photo_path(number), media_type="image/jpeg")

    @app.post("/api/returns/stage")
    async def stage(request: Request, body: Stage):
        return {"parcels": warehouse.set_staged([p.model_dump() for p in body.parcels], body.staged, actor(request))}
