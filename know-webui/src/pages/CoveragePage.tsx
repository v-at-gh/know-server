import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useDomains } from "../api/queries";
import { ALL_LANGS, DEFAULT_LANGS } from "../lib/format";
import { StatusBadge } from "../components/StatusBadge";

export const CoveragePage: React.FC = () => {
	const [pattern, setPattern] = useState("");
	const [selectedLangs, setSelectedLangs] = useState<string[]>(() => [...DEFAULT_LANGS]);
	const navigate = useNavigate();

	const { data, isLoading, error } = useDomains(pattern || undefined, selectedLangs, true);

	const toggleLang = (lang: string) => {
		if (selectedLangs.includes(lang)) {
			if (selectedLangs.length > 1) {
				setSelectedLangs(selectedLangs.filter((l) => l !== lang));
			}
		} else {
			setSelectedLangs([...selectedLangs, lang]);
		}
	};

	return (
		<div className="space-y-6">
			<div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
				<div>
					<h2 className="text-2xl font-bold text-slate-900 dark:text-slate-100 tracking-tight">Coverage Matrix</h2>
					<p className="text-sm text-slate-600 dark:text-slate-400 mt-1">Real-time domain translation and coverage status across languages.</p>
				</div>
				<div className="w-full md:w-72">
					<input
						type="text"
						value={pattern}
						onChange={(e) => setPattern(e.target.value)}
						placeholder="Filter domains..."
						className="w-full px-4 py-2 bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-800 rounded-lg text-sm text-slate-900 dark:text-slate-200 focus:outline-none focus:border-indigo-500 transition-colors shadow-sm"
					/>
				</div>
			</div>

			{/* Language Chips */}
			<div className="flex flex-wrap gap-1.5 items-center bg-white dark:bg-slate-900 p-4 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm">
				<span className="text-xs font-semibold uppercase text-slate-600 dark:text-slate-400 mr-2 tracking-wider">Languages:</span>
				{ALL_LANGS.map((lang) => {
					const isSelected = selectedLangs.includes(lang);
					return (
						<button
							key={lang}
							onClick={() => toggleLang(lang)}
							className={`px-2.5 py-1 rounded-md text-xs font-mono uppercase font-medium transition-colors border ${isSelected
									? "bg-indigo-600 text-white border-indigo-500 shadow"
									: "bg-slate-100 dark:bg-slate-800/80 text-slate-700 dark:text-slate-400 border-slate-300 dark:border-slate-700/60 hover:text-slate-900 dark:hover:text-slate-200 hover:bg-slate-200 dark:hover:bg-slate-800"
								}`}
						>
							{lang}
						</button>
					);
				})}
			</div>

			{/* Matrix Table */}
			{isLoading && (
				<div className="py-20 text-center text-slate-500 dark:text-slate-400 flex flex-col items-center justify-center gap-3">
					<div className="w-8 h-8 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
					Loading coverage matrix...
				</div>
			)}

			{error && (
				<div className="p-4 rounded-lg bg-rose-100 dark:bg-rose-950/60 border border-rose-300 dark:border-rose-900/80 text-rose-800 dark:text-rose-300 text-sm">
					Failed to load domains: {error.message}
				</div>
			)}

			{data && (
				<div className="overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-sm">
					<table className="w-full text-left text-sm text-slate-700 dark:text-slate-300">
						<thead className="bg-slate-100 dark:bg-slate-800/80 text-xs uppercase text-slate-600 dark:text-slate-400 border-b border-slate-200 dark:border-slate-700/60">
							<tr>
								<th className="px-4 py-3 font-semibold sticky left-0 bg-slate-100 dark:bg-slate-800/90 z-10">Domain</th>
								<th className="px-4 py-3 font-semibold">Kind</th>
								{selectedLangs.map((lang) => (
									<th key={lang} className="px-3 py-3 font-mono uppercase text-center font-semibold">
										{lang}
									</th>
								))}
							</tr>
						</thead>
						<tbody className="divide-y divide-slate-200 dark:divide-slate-800/60">
							{data.domains.map((domain) => (
								<tr key={domain.slug} className="hover:bg-slate-50 dark:hover:bg-slate-800/40 transition-colors">
									<td className="px-4 py-3 font-medium text-slate-900 dark:text-slate-200 sticky left-0 bg-white dark:bg-slate-900 z-10">
										<button
											onClick={() => navigate(`/domains/${domain.slug}`)}
											className="text-indigo-600 dark:text-indigo-400 hover:text-indigo-500 dark:hover:text-indigo-300 font-medium text-left truncate max-w-xs block"
										>
											{domain.name}
											<span className="block text-xs text-slate-500 dark:text-slate-500 font-mono">{domain.slug}</span>
										</button>
									</td>
									<td className="px-4 py-3 text-xs font-mono text-slate-500 dark:text-slate-400">{domain.kind}</td>
									{selectedLangs.map((lang) => {
										const st = domain.status[lang];
										return (
											<td key={lang} className="px-3 py-3 text-center">
												<StatusBadge
													status={st}
													compact
													onClick={() => navigate(`/domains/${domain.slug}?lang=${lang}`)}
												/>
											</td>
										);
									})}
								</tr>
							))}
							{data.domains.length === 0 && (
								<tr>
									<td colSpan={2 + selectedLangs.length} className="py-12 text-center text-slate-500 dark:text-slate-500">
										No domains found matching pattern.
									</td>
								</tr>
							)}
						</tbody>
					</table>
				</div>
			)}
		</div>
	);
};
