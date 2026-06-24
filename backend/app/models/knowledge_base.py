from pydantic import BaseModel, ConfigDict, Field


class DriveFolder(BaseModel):
    id: str
    name: str
    modified_at: str | None = Field(default=None, alias="modifiedAt")
    web_view_link: str | None = Field(default=None, alias="webViewLink")
    child_folder_count: int = Field(default=0, alias="childFolderCount")

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class SupermemoryConnection(BaseModel):
    id: str
    provider: str | None = None
    status: str | None = None
    created_at: str | None = Field(default=None, alias="createdAt")

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class KnowledgeBaseStatus(BaseModel):
    supermemory_configured: bool = Field(alias="supermemoryConfigured")
    google_drive_configured: bool = Field(alias="googleDriveConfigured")
    drive_connected: bool = Field(alias="driveConnected")
    shared_drive_name: str = Field(alias="sharedDriveName")
    shared_drive_id: str | None = Field(default=None, alias="sharedDriveId")
    folder_count: int = Field(default=0, alias="folderCount")
    container_tag: str = Field(alias="containerTag")

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class KnowledgeBaseDocument(BaseModel):
    id: str
    title: str
    category: str
    category_title: str = Field(alias="categoryTitle")
    file_name: str = Field(alias="fileName")
    mime_type: str = Field(alias="mimeType")
    file_size: int = Field(alias="fileSize")
    uploaded_at: str = Field(alias="uploadedAt")
    supermemory_custom_id: str | None = Field(default=None, alias="supermemoryCustomId")
    supermemory_synced_at: str | None = Field(default=None, alias="supermemorySyncedAt")
    supermemory_error: str | None = Field(default=None, alias="supermemoryError")
    supermemory_status: str | None = Field(default=None, alias="supermemoryStatus")
    supermemory_url: str | None = Field(default=None, alias="supermemoryUrl")

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class KnowledgeBaseDocumentsResponse(BaseModel):
    documents: list[KnowledgeBaseDocument]
    container_tag: str = Field(alias="containerTag")

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class KnowledgeBaseFoldersResponse(BaseModel):
    shared_drive_name: str = Field(alias="sharedDriveName")
    shared_drive_id: str | None = Field(default=None, alias="sharedDriveId")
    folders: list[DriveFolder]
    source: str

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class ConnectDriveResponse(BaseModel):
    auth_link: str = Field(alias="authLink")
    expires_in: str | None = Field(default=None, alias="expiresIn")

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)
