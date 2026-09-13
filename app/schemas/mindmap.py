from pydantic import BaseModel, Field


class NodeData(BaseModel):
    id: str
    type: str
    label: str
    source: str = "system"
    metadata: dict | None = None
    position: dict | None = None


class EdgeData(BaseModel):
    source: str
    target: str


class MindMapData(BaseModel):
    nodes: list[NodeData] = []
    edges: list[EdgeData] = []


class GenerateNodeRequest(BaseModel):
    parent_node_id: str | None = None
    prompt: str = Field(min_length=1, max_length=1000)


class GenerateNodeResponse(BaseModel):
    node: NodeData
    edge: EdgeData


class RenameNodeRequest(BaseModel):
    node_id: str
    new_label: str = Field(min_length=1, max_length=500)


class DeleteNodeRequest(BaseModel):
    node_id: str


class AutoSaveRequest(BaseModel):
    # Topes para que un cliente no pueda llenar la base de datos con un mapa desmesurado; un mapa
    # real tiene decenas de nodos.
    nodes: list[dict] = Field(default=[], max_length=2000)
    edges: list[dict] = Field(default=[], max_length=4000)
