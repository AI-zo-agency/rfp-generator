export interface DriveFolder {
  id: string;
  name: string;
  modifiedAt?: string | null;
  webViewLink?: string | null;
  childFolderCount?: number;
}

export interface KnowledgeBaseStatus {
  supermemoryConfigured: boolean;
  googleDriveConfigured: boolean;
  driveConnected: boolean;
  sharedDriveName: string;
  sharedDriveId?: string | null;
  folderCount: number;
  containerTag: string;
}

export interface KnowledgeBaseFoldersResponse {
  sharedDriveName: string;
  sharedDriveId?: string | null;
  folders: DriveFolder[];
  source: string;
}

const BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL || process.env.BACKEND_URL || "http://localhost:8001";

async function backendFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BACKEND}/api/v1${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });

  const body = (await response.json()) as T & { detail?: string };
  if (!response.ok) {
    throw new Error(body.detail ?? `Request failed (${response.status})`);
  }
  return body;
}

export async function fetchKnowledgeBaseStatus(): Promise<KnowledgeBaseStatus> {
  return backendFetch<KnowledgeBaseStatus>("/knowledge-base/status");
}

export async function fetchDriveFolders(): Promise<KnowledgeBaseFoldersResponse> {
  return backendFetch<KnowledgeBaseFoldersResponse>("/knowledge-base/folders");
}

export async function connectGoogleDrive(): Promise<{ authLink: string }> {
  return backendFetch<{ authLink: string }>("/knowledge-base/connect/google-drive", {
    method: "POST",
  });
}

export async function syncGoogleDrive(): Promise<void> {
  await backendFetch("/knowledge-base/sync/google-drive", { method: "POST" });
}
