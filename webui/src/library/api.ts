export class LibraryRequestError extends Error {
  constructor(
    message: string,
    public data: unknown,
    public status: number,
  ) {
    super(message);
  }
}

export async function libraryRequest<T>(
  url: string,
  options?: RequestInit,
): Promise<T> {
  const response = await fetch(url, options);
  if (!response.headers.get("content-type")?.includes("application/json")) {
    throw new Error("无法读取资源库，请确认已登录并刷新页面。");
  }
  const data = await response.json();
  if (!response.ok || data.success === false)
    throw new LibraryRequestError(
      data.message || "资源库请求失败",
      data,
      response.status,
    );
  return data as T;
}
