import { DomainState, CellStatus } from "../api/types";

export const ALL_LANGS = [
	"en", "ru", "uk", "es", "de", "fr", "sr", "cs", "az", "hy", "ka",
	"zh", "hi", "ar", "bn", "pt", "id", "ur", "vi", "tr", "it",
	"kk", "tg", "uz",
] as const;

export const DEFAULT_LANGS = ["en", "ru", "uk"];

export function formatDate(iso?: string): string {
	if (!iso) return "—";
	try {
		return new Date(iso).toLocaleString();
	} catch {
		return iso;
	}
}

export function pluralize(n: number, singular: string, plural: string): string {
	return `${n} ${n === 1 ? singular : plural}`;
}

export function stateColorClass(state?: DomainState | string): string {
	switch (state) {
		case "bootstrapped":
		case "ready":
			return "bg-emerald-100 dark:bg-emerald-950/60 text-emerald-800 dark:text-emerald-400 border-emerald-300 dark:border-emerald-800/60";
		case "partial":
		case "short":
			return "bg-amber-100 dark:bg-amber-950/60 text-amber-800 dark:text-amber-400 border-amber-300 dark:border-amber-800/60";
		case "broken":
		case "absent_source":
			return "bg-rose-100 dark:bg-rose-950/60 text-rose-800 dark:text-rose-400 border-rose-300 dark:border-rose-800/60";
		case "archived":
			return "bg-purple-100 dark:bg-purple-950/60 text-purple-800 dark:text-purple-400 border-purple-300 dark:border-purple-800/60";
		case "missing":
			return "bg-slate-100 dark:bg-slate-800/60 text-slate-700 dark:text-slate-400 border-slate-300 dark:border-slate-700/60";
		default:
			return "bg-slate-100 dark:bg-slate-900 text-slate-600 dark:text-slate-500 border-slate-300 dark:border-slate-800";
	}
}

export function cellColorClass(status: CellStatus): string {
	switch (status) {
		case "ok":
			return "bg-emerald-100 dark:bg-emerald-950/60 text-emerald-800 dark:text-emerald-400 border-emerald-300 dark:border-emerald-800/60";
		case "thin":
			return "bg-amber-100 dark:bg-amber-950/60 text-amber-800 dark:text-amber-400 border-amber-300 dark:border-amber-800/60";
		case "incomplete":
			return "bg-rose-100 dark:bg-rose-950/60 text-rose-800 dark:text-rose-400 border-rose-300 dark:border-rose-800/60";
		case "absent":
			return "bg-slate-200 dark:bg-slate-800 text-slate-700 dark:text-slate-400 border-slate-300 dark:border-slate-700";
		case "unknown":
		default:
			return "bg-slate-100 dark:bg-slate-900 text-slate-500 dark:text-slate-500 border-slate-300 dark:border-slate-800";
	}
}

export function cellLabel(status: CellStatus): string {
	return status;
}
