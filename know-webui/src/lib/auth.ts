const TOKEN_KEY = "know_token";

export function getToken(): string | null {
	return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
	localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
	localStorage.removeItem(TOKEN_KEY);
}

export async function login(username: string, password: string): Promise<{ token: string }> {
	const res = await fetch("/admin-api/v1/auth/token", {
		method: "POST",
		headers: {
			"Content-Type": "application/json",
		},
		body: JSON.stringify({ username, password }),
	});
	if (!res.ok) {
		const text = await res.text();
		throw new Error(text || `Login failed (HTTP ${res.status})`);
	}
	const data = await res.json();
	if (data.token) {
		setToken(data.token);
	} else if (data.access_token) {
		setToken(data.access_token);
	}
	return data;
}
