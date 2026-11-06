import { useState, useEffect } from "react";

export type ThemePreference = "system" | "dark" | "light";

const STORAGE_KEY = "know_theme_preference";

export function getThemePreference(): ThemePreference {
	const val = localStorage.getItem(STORAGE_KEY);
	if (val === "dark" || val === "light" || val === "system") {
		return val;
	}
	return "system";
}

export function setThemePreference(pref: ThemePreference): void {
	localStorage.setItem(STORAGE_KEY, pref);
}

export function resolveTheme(pref: ThemePreference): "dark" | "light" {
	if (pref === "dark") return "dark";
	if (pref === "light") return "light";
	return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function useTheme() {
	const [preference, setPreferenceState] = useState<ThemePreference>(getThemePreference);

	useEffect(() => {
		const applyTheme = () => {
			const resolved = resolveTheme(preference);
			if (resolved === "dark") {
				document.documentElement.classList.add("dark");
			} else {
				document.documentElement.classList.remove("dark");
			}
		};

		applyTheme();

		if (preference === "system") {
			const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
			const handler = (e: MediaQueryListEvent) => {
				if (e.matches) {
					document.documentElement.classList.add("dark");
				} else {
					document.documentElement.classList.remove("dark");
				}
			};
			mediaQuery.addEventListener("change", handler);
			return () => mediaQuery.removeEventListener("change", handler);
		}
	}, [preference]);

	const setPreference = (newPref: ThemePreference) => {
		setThemePreference(newPref);
		setPreferenceState(newPref);
	};

	return {
		preference,
		setPreference,
		resolvedTheme: resolveTheme(preference),
	};
}
