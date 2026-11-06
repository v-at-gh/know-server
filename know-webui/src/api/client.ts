import { getToken, clearToken } from "../lib/auth";

const adminBase = "/admin-api";
const orchBase = "/orch-api";

async function apiFetch(base: string, path: string, init: RequestInit = {}) {
	const token = getToken();
	const headers: Record<string, string> = {
		"Content-Type": "application/json",
		...(init.headers as Record<string, string> | undefined),
	};
	if (token) headers["Authorization"] = `Bearer ${token}`;

	const res = await fetch(`${base}${path}`, { ...init, headers });

	if (res.status === 401 || res.status === 403) {
		clearToken();
		window.location.href = "/login";
		throw new Error("Unauthorized");
	}

	if (!res.ok) {
		const contentType = res.headers.get("content-type") || "";
		const errorText = contentType.includes("application/json")
			? JSON.stringify(await res.json())
			: await res.text();
		throw new Error(`HTTP ${res.status}: ${errorText}`);
	}

	return res.json();
}

export const adminGet = (p: string) => apiFetch(adminBase, p);
export const adminPost = (p: string, body: unknown) =>
	apiFetch(adminBase, p, { method: "POST", body: JSON.stringify(body) });
export const adminPut = (p: string, body: unknown) =>
	apiFetch(adminBase, p, { method: "PUT", body: JSON.stringify(body) });
export const orchGet = (p: string) => apiFetch(orchBase, p);
export const orchPost = (p: string, body: unknown) =>
	apiFetch(orchBase, p, { method: "POST", body: JSON.stringify(body) });
