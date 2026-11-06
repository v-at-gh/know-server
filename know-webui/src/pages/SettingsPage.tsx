import React, { useState, useEffect, useMemo } from "react";
import { useUserSettings, useUpdateUserSettings, useDomains } from "../api/queries";
import { UserSettingsPatch } from "../api/types";

const ALL_LANGS = [
	"en", "ru", "uk", "es", "de", "fr", "sr", "cs", "az", "hy", "ka",
	"zh", "hi", "ar", "bn", "pt", "id", "ur", "vi", "tr", "it", "kk", "tg", "uz"
];

interface PerDomainRow {
	domain: string;
	articles?: number;
	select?: number;
	construct?: number;
}

interface PerLangRow {
	lang: string;
	translateDomains: string; // "*" or "domain1, domain2"
}

export const SettingsPage: React.FC = () => {
	const { data: settings, isLoading, error } = useUserSettings();
	const { data: domainsData } = useDomains();
	const updateMutation = useUpdateUserSettings();

	const [activeDomains, setActiveDomains] = useState<string[]>([]);
	const [activeLangs, setActiveLangs] = useState<string[]>([]);
	const [favorites, setFavorites] = useState<string[]>([]);
	const [articlesPerDomain, setArticlesPerDomain] = useState<number>(10);
	const [selectCount, setSelectCount] = useState<number>(5);
	const [constructCount, setConstructCount] = useState<number>(0);
	const [perDomainRows, setPerDomainRows] = useState<PerDomainRow[]>([]);
	const [perLangRows, setPerLangRows] = useState<PerLangRow[]>([]);

	const [successMsg, setSuccessMsg] = useState<string | null>(null);
	const [errorMsg, setErrorMsg] = useState<string | null>(null);
	const [prunedWarning, setPrunedWarning] = useState<string[]>([]);

	useEffect(() => {
		if (!settings) return;
		setActiveDomains(settings.profile.active_domains || []);
		setActiveLangs(settings.profile.active_langs || []);
		setFavorites(settings.favorites || []);
		setArticlesPerDomain(settings.generation_params.articles_per_domain || 10);
		setSelectCount(settings.generation_params.questions_types?.select ?? 5);
		setConstructCount(settings.generation_params.questions_types?.construct ?? 0);

		// Transform per_domain dict -> array of rows
		const pd = settings.generation_params.per_domain || {};
		const pdRows: PerDomainRow[] = Object.entries(pd).map(([dom, val]) => ({
			domain: dom,
			articles: val.articles_per_domain,
			select: val.questions_types?.select,
			construct: val.questions_types?.construct,
		}));
		setPerDomainRows(pdRows);

		// Transform per_lang dict -> array of rows
		const pl = settings.generation_params.per_lang || {};
		const plRows: PerLangRow[] = Object.entries(pl).map(([lang, val]) => {
			const td = val.translate_domains;
			const strVal = Array.isArray(td) ? td.join(", ") : (td ?? "*");
			return { lang, translateDomains: strVal };
		});
		setPerLangRows(plRows);
	}, [settings]);

	const allDomains = useMemo(
		() => (domainsData?.domains ?? []).map((d) => d.slug).sort(),
		[domainsData]
	);

	const handleSave = async (e: React.FormEvent) => {
		e.preventDefault();
		setSuccessMsg(null);
		setErrorMsg(null);
		setPrunedWarning([]);

		const totalQ = selectCount + constructCount;
		if (totalQ < 1 || totalQ > 20) {
			setErrorMsg("Total global questions per article (Select + Construct) must be between 1 and 20.");
			return;
		}
		if (articlesPerDomain < 1 || articlesPerDomain > 30) {
			setErrorMsg("Global articles per domain must be between 1 and 30.");
			return;
		}

		const invalidFav = favorites.filter((f) => !activeDomains.includes(f));
		if (invalidFav.length > 0) {
			setErrorMsg(`Favorites must be a subset of active domains. Invalid: ${invalidFav.join(", ")}`);
			return;
		}

		// Reconstruct per_domain dict
		const perDomainObj: Record<string, any> = {};
		for (const row of perDomainRows) {
			if (!row.domain) continue;
			const domGp: Record<string, any> = {};
			if (row.articles !== undefined && !isNaN(row.articles)) {
				domGp.articles_per_domain = row.articles;
			}
			if ((row.select !== undefined && !isNaN(row.select)) || (row.construct !== undefined && !isNaN(row.construct))) {
				domGp.questions_types = {};
				if (row.select !== undefined && !isNaN(row.select)) domGp.questions_types.select = row.select;
				if (row.construct !== undefined && !isNaN(row.construct)) domGp.questions_types.construct = row.construct;
			}
			if (Object.keys(domGp).length > 0) {
				perDomainObj[row.domain] = domGp;
			}
		}

		// Reconstruct per_lang dict
		const perLangObj: Record<string, any> = {};
		for (const row of perLangRows) {
			if (!row.lang) continue;
			const rawTd = row.translateDomains.trim();
			let tdVal: "*" | string[] = "*";
			if (rawTd === "*") {
				tdVal = "*";
			} else if (rawTd === "") {
				tdVal = [];
			} else {
				tdVal = rawTd.split(",").map((s) => s.trim()).filter(Boolean);
			}
			perLangObj[row.lang] = { translate_domains: tdVal };
		}

		const patch: UserSettingsPatch = {
			active_domains: activeDomains,
			active_langs: activeLangs,
			favorites,
			generation_params: {
				articles_per_domain: articlesPerDomain,
				questions_types: { select: selectCount, construct: constructCount },
				per_domain: Object.keys(perDomainObj).length ? perDomainObj : undefined,
				per_lang: Object.keys(perLangObj).length ? perLangObj : undefined,
			},
		};

		try {
			const result = await updateMutation.mutateAsync(patch);
			const pruned = result.pruned_favorites ?? [];
			if (pruned.length > 0) {
				setPrunedWarning(pruned);
			}
			setSuccessMsg("Settings saved successfully.");
		} catch (err: any) {
			setErrorMsg(err?.message || "Failed to save user settings.");
		}
	};

	if (isLoading) {
		return <div className="p-6 text-slate-500">Loading user settings...</div>;
	}

	if (error) {
		return (
			<div className="p-6 text-rose-700 bg-rose-50 dark:bg-rose-950/40 rounded-xl border border-rose-200">
				Failed to load user settings: {(error as Error).message}
			</div>
		);
	}

	return (
		<div className="max-w-4xl mx-auto p-6 space-y-8 bg-white dark:bg-slate-900 rounded-xl shadow-sm border border-slate-200 dark:border-slate-800">
			<div>
				<h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100 tracking-tight">User Settings</h1>
				<p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
					Configure profile preferences, content generation targets, favorites, and per-domain/lang overrides.
				</p>
			</div>

			{successMsg && (
				<div className="p-3 bg-emerald-50 dark:bg-emerald-950/30 text-emerald-700 dark:text-emerald-300 text-sm rounded-lg border border-emerald-200 dark:border-emerald-900">
					{successMsg}
				</div>
			)}

			{errorMsg && (
				<div className="p-3 bg-rose-50 dark:bg-rose-950/30 text-rose-700 dark:text-rose-300 text-sm rounded-lg border border-rose-200 dark:border-rose-900">
					{errorMsg}
				</div>
			)}

			{prunedWarning.length > 0 && (
				<div className="p-3 bg-amber-50 dark:bg-amber-950/40 text-amber-800 dark:text-amber-200 text-sm rounded-lg border border-amber-300 dark:border-amber-800">
					<strong>Favorites auto-removed (no longer in active domains):</strong> {prunedWarning.join(", ")}
				</div>
			)}

			<form onSubmit={handleSave} className="space-y-8">
				{/* Section 1: Profile */}
				<section className="space-y-4 border-b border-slate-200 dark:border-slate-800 pb-6">
					<h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">Profile & Active Preferences</h2>

					<div>
						<div className="flex items-center justify-between mb-2">
							<label className="block text-sm font-medium text-slate-700 dark:text-slate-300">
								Active Domains ({activeDomains.length} selected)
							</label>
							<div className="text-xs space-x-2">
								<button
									type="button"
									onClick={() => setActiveDomains([...allDomains])}
									className="text-indigo-600 dark:text-indigo-400 hover:underline"
								>
									Select All
								</button>
								<button
									type="button"
									onClick={() => {
										setActiveDomains([]);
										setFavorites([]);
									}}
									className="text-slate-500 hover:underline"
								>
									Clear All
								</button>
							</div>
						</div>
						<div className="grid grid-cols-2 md:grid-cols-3 gap-2 max-h-48 overflow-y-auto p-3 border border-slate-200 dark:border-slate-800 rounded-lg bg-slate-50 dark:bg-slate-800/50">
							{allDomains.map((d) => (
								<label key={d} className="flex items-center gap-2 text-xs font-mono text-slate-700 dark:text-slate-300">
									<input
										type="checkbox"
										checked={activeDomains.includes(d)}
										onChange={(e) => {
											if (e.target.checked) {
												setActiveDomains([...activeDomains, d]);
											} else {
												setActiveDomains(activeDomains.filter((x) => x !== d));
												setFavorites(favorites.filter((f) => f !== d));
											}
										}}
										className="rounded text-indigo-600 focus:ring-indigo-500"
									/>
									{d}
								</label>
							))}
						</div>
					</div>

					<div>
						<div className="flex items-center justify-between mb-2">
							<label className="block text-sm font-medium text-slate-700 dark:text-slate-300">
								Active Languages ({activeLangs.length} selected)
							</label>
							<div className="text-xs space-x-2">
								<button
									type="button"
									onClick={() => setActiveLangs([...ALL_LANGS])}
									className="text-indigo-600 dark:text-indigo-400 hover:underline"
								>
									Select All
								</button>
								<button
									type="button"
									onClick={() => setActiveLangs([])}
									className="text-slate-500 hover:underline"
								>
									Clear All
								</button>
							</div>
						</div>
						<div className="grid grid-cols-4 md:grid-cols-6 gap-2 p-3 border border-slate-200 dark:border-slate-800 rounded-lg bg-slate-50 dark:bg-slate-800/50">
							{ALL_LANGS.map((l) => (
								<label key={l} className="flex items-center gap-2 text-xs font-mono text-slate-700 dark:text-slate-300 uppercase">
									<input
										type="checkbox"
										checked={activeLangs.includes(l)}
										onChange={(e) => {
											if (e.target.checked) {
												setActiveLangs([...activeLangs, l]);
											} else {
												setActiveLangs(activeLangs.filter((x) => x !== l));
											}
										}}
										className="rounded text-indigo-600 focus:ring-indigo-500"
									/>
									{l}
								</label>
							))}
						</div>
					</div>

					<div>
						<label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
							Favorites (subset of active domains)
						</label>
						<p className="text-xs text-slate-500 mb-2">
							Favorites offer quick access across dashboard views and filter options.
						</p>
						{activeDomains.length === 0 ? (
							<div className="text-xs text-slate-400 italic p-3 border border-dashed rounded-lg">
								Select active domains above to configure favorites.
							</div>
						) : (
							<div className="grid grid-cols-2 md:grid-cols-3 gap-2 max-h-36 overflow-y-auto p-3 border border-slate-200 dark:border-slate-800 rounded-lg bg-slate-50 dark:bg-slate-800/50">
								{activeDomains.map((d) => (
									<label key={d} className="flex items-center gap-2 text-xs font-mono text-slate-700 dark:text-slate-300">
										<input
											type="checkbox"
											checked={favorites.includes(d)}
											onChange={(e) => {
												if (e.target.checked) {
													setFavorites([...favorites, d]);
												} else {
													setFavorites(favorites.filter((f) => f !== d));
												}
											}}
											className="rounded text-amber-500 focus:ring-amber-400"
										/>
										★ {d}
									</label>
								))}
							</div>
						)}
					</div>
				</section>

				{/* Section 2: Generation Global */}
				<section className="space-y-4 border-b border-slate-200 dark:border-slate-800 pb-6">
					<h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">Global Generation Targets</h2>

					<div className="grid grid-cols-1 md:grid-cols-3 gap-4">
						<div>
							<label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
								Articles Per Domain (1-30)
							</label>
							<input
								type="number"
								min={1}
								max={30}
								value={articlesPerDomain}
								onChange={(e) => setArticlesPerDomain(parseInt(e.target.value) || 1)}
								className="w-full px-3 py-2 border border-slate-300 dark:border-slate-700 rounded-lg bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 text-sm"
							/>
						</div>

						<div>
							<label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
								Select Questions / Article
							</label>
							<input
								type="number"
								min={0}
								max={20}
								value={selectCount}
								onChange={(e) => setSelectCount(parseInt(e.target.value) || 0)}
								className="w-full px-3 py-2 border border-slate-300 dark:border-slate-700 rounded-lg bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 text-sm"
							/>
						</div>

						<div>
							<label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
								Construct Questions / Article
							</label>
							<input
								type="number"
								min={0}
								max={20}
								value={constructCount}
								onChange={(e) => setConstructCount(parseInt(e.target.value) || 0)}
								className="w-full px-3 py-2 border border-slate-300 dark:border-slate-700 rounded-lg bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 text-sm"
							/>
						</div>
					</div>
					<p className="text-xs text-slate-500">
						Total questions per article (Select + Construct) must be between 1 and 20.
					</p>
				</section>

				{/* Section 3: Per-Domain Overrides */}
				<section className="space-y-4 border-b border-slate-200 dark:border-slate-800 pb-6">
					<div className="flex items-center justify-between">
						<div>
							<h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">Per-Domain Overrides</h2>
							<p className="text-xs text-slate-500">Override global target limits for specific domains.</p>
						</div>
						<button
							type="button"
							onClick={() => {
								const unused = allDomains.find((d) => !perDomainRows.some((r) => r.domain === d));
								setPerDomainRows([...perDomainRows, { domain: unused || "" }]);
							}}
							className="px-3 py-1.5 bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 text-xs font-medium rounded-lg text-slate-700 dark:text-slate-300 transition-colors"
						>
							+ Add Domain Override
						</button>
					</div>

					{perDomainRows.length === 0 ? (
						<div className="text-xs text-slate-400 italic p-3 border border-dashed rounded-lg">
							No per-domain overrides configured. All domains inherit global targets.
						</div>
					) : (
						<div className="space-y-3">
							{perDomainRows.map((row, idx) => (
								<div key={idx} className="flex items-center gap-3 p-3 border border-slate-200 dark:border-slate-800 rounded-lg bg-slate-50 dark:bg-slate-800/40">
									<select
										value={row.domain}
										onChange={(e) => {
											const updated = [...perDomainRows];
											updated[idx].domain = e.target.value;
											setPerDomainRows(updated);
										}}
										className="flex-1 px-3 py-1.5 border border-slate-300 dark:border-slate-700 rounded-lg bg-white dark:bg-slate-800 text-xs font-mono text-slate-900 dark:text-slate-100"
									>
										<option value="">-- select domain --</option>
										{allDomains.map((d) => (
											<option key={d} value={d}>{d}</option>
										))}
									</select>

									<input
										type="number"
										placeholder="Articles"
										min={1}
										max={30}
										value={row.articles ?? ""}
										onChange={(e) => {
											const updated = [...perDomainRows];
											updated[idx].articles = e.target.value === "" ? undefined : parseInt(e.target.value);
											setPerDomainRows(updated);
										}}
										className="w-24 px-2 py-1.5 border border-slate-300 dark:border-slate-700 rounded-lg bg-white dark:bg-slate-800 text-xs text-slate-900 dark:text-slate-100"
									/>

									<input
										type="number"
										placeholder="Select Q"
										min={0}
										max={20}
										value={row.select ?? ""}
										onChange={(e) => {
											const updated = [...perDomainRows];
											updated[idx].select = e.target.value === "" ? undefined : parseInt(e.target.value);
											setPerDomainRows(updated);
										}}
										className="w-24 px-2 py-1.5 border border-slate-300 dark:border-slate-700 rounded-lg bg-white dark:bg-slate-800 text-xs text-slate-900 dark:text-slate-100"
									/>

									<input
										type="number"
										placeholder="Construct Q"
										min={0}
										max={20}
										value={row.construct ?? ""}
										onChange={(e) => {
											const updated = [...perDomainRows];
											updated[idx].construct = e.target.value === "" ? undefined : parseInt(e.target.value);
											setPerDomainRows(updated);
										}}
										className="w-24 px-2 py-1.5 border border-slate-300 dark:border-slate-700 rounded-lg bg-white dark:bg-slate-800 text-xs text-slate-900 dark:text-slate-100"
									/>

									<button
										type="button"
										onClick={() => setPerDomainRows(perDomainRows.filter((_, i) => i !== idx))}
										className="text-rose-600 hover:text-rose-800 text-xs font-bold px-2 py-1"
									>
										✕
									</button>
								</div>
							))}
						</div>
					)}
				</section>

				{/* Section 4: Per-Language Overrides */}
				<section className="space-y-4 border-b border-slate-200 dark:border-slate-800 pb-6">
					<div className="flex items-center justify-between">
						<div>
							<h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">Per-Language Translation Rules</h2>
							<p className="text-xs text-slate-500">
								Restrict translation domain scope per language (`*` for all domains, or comma-separated list).
							</p>
						</div>
						<button
							type="button"
							onClick={() => {
								const unused = ALL_LANGS.find((l) => l !== "en" && !perLangRows.some((r) => r.lang === l));
								setPerLangRows([...perLangRows, { lang: unused || "", translateDomains: "*" }]);
							}}
							className="px-3 py-1.5 bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 text-xs font-medium rounded-lg text-slate-700 dark:text-slate-300 transition-colors"
						>
							+ Add Language Rule
						</button>
					</div>

					{perLangRows.length === 0 ? (
						<div className="text-xs text-slate-400 italic p-3 border border-dashed rounded-lg">
							No per-language restrictions configured. All languages translate all active domains.
						</div>
					) : (
						<div className="space-y-3">
							{perLangRows.map((row, idx) => (
								<div key={idx} className="flex items-center gap-3 p-3 border border-slate-200 dark:border-slate-800 rounded-lg bg-slate-50 dark:bg-slate-800/40">
									<select
										value={row.lang}
										onChange={(e) => {
											const updated = [...perLangRows];
											updated[idx].lang = e.target.value;
											setPerLangRows(updated);
										}}
										className="w-32 px-3 py-1.5 border border-slate-300 dark:border-slate-700 rounded-lg bg-white dark:bg-slate-800 text-xs font-mono uppercase text-slate-900 dark:text-slate-100"
									>
										<option value="">-- lang --</option>
										{ALL_LANGS.filter((l) => l !== "en").map((l) => (
											<option key={l} value={l}>{l}</option>
										))}
									</select>

									<input
										type="text"
										placeholder="e.g. * or linux, docker"
										value={row.translateDomains}
										onChange={(e) => {
											const updated = [...perLangRows];
											updated[idx].translateDomains = e.target.value;
											setPerLangRows(updated);
										}}
										className="flex-1 px-3 py-1.5 border border-slate-300 dark:border-slate-700 rounded-lg bg-white dark:bg-slate-800 text-xs font-mono text-slate-900 dark:text-slate-100"
									/>

									<button
										type="button"
										onClick={() => setPerLangRows(perLangRows.filter((_, i) => i !== idx))}
										className="text-rose-600 hover:text-rose-800 text-xs font-bold px-2 py-1"
									>
										✕
									</button>
								</div>
							))}
						</div>
					)}
				</section>

				{/* Save Button */}
				<div className="flex justify-end pt-2">
					<button
						type="submit"
						disabled={updateMutation.isPending}
						className="px-6 py-2 bg-indigo-600 hover:bg-indigo-700 text-white font-medium rounded-lg text-sm transition-colors disabled:opacity-50 shadow-sm"
					>
						{updateMutation.isPending ? "Saving Settings..." : "Save Settings"}
					</button>
				</div>
			</form>
		</div>
	);
};
