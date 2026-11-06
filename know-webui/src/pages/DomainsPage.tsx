import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useDomains } from "../api/queries";
import { DEFAULT_LANGS, ALL_LANGS } from "../lib/format";
import { StatusBadge } from "../components/StatusBadge";
import { DataTable } from "../components/DataTable";
import { DomainEntry } from "../api/types";

export const DomainsPage: React.FC = () => {
	const [pattern, setPattern] = useState("");
	const [selectedLangs, setSelectedLangs] = useState<string[]>(() => [...DEFAULT_LANGS]);
	const navigate = useNavigate();

	const { data, isLoading, error } = useDomains(pattern || undefined, selectedLangs, true);

	const columns = [
		{
			header: "Domain / Slug",
			accessor: (domain: DomainEntry) => (
				<div>
					<div className="font-medium text-slate-900 dark:text-slate-200">{domain.name}</div>
					<div className="text-xs font-mono text-slate-500">{domain.slug}</div>
				</div>
			),
		},
		{
			header: "Kind",
			accessor: (domain: DomainEntry) => <span className="font-mono text-xs text-slate-600 dark:text-slate-400">{domain.kind}</span>,
		},
		...selectedLangs.map((lang) => ({
			header: lang.toUpperCase(),
			className: "text-center",
			accessor: (domain: DomainEntry) => (
				<div className="flex justify-center">
					<StatusBadge
						status={domain.status[lang]}
						onClick={() => navigate(`/domains/${domain.slug}?lang=${lang}`)}
					/>
				</div>
			),
		})),
	];

	return (
		<div className="space-y-6">
			<div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
				<div>
					<h2 className="text-2xl font-bold text-slate-900 dark:text-slate-100 tracking-tight">Domains Directory</h2>
					<p className="text-sm text-slate-600 dark:text-slate-400 mt-1">Manage domains and inspect translation status per language.</p>
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
							onClick={() => {
								if (isSelected) {
									if (selectedLangs.length > 1) setSelectedLangs(selectedLangs.filter((l) => l !== lang));
								} else {
									setSelectedLangs([...selectedLangs, lang]);
								}
							}}
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

			{isLoading && (
				<div className="py-20 text-center text-slate-500 dark:text-slate-400 flex flex-col items-center justify-center gap-3">
					<div className="w-8 h-8 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
					Loading domains...
				</div>
			)}

			{error && (
				<div className="p-4 rounded-lg bg-rose-100 dark:bg-rose-950/60 border border-rose-300 dark:border-rose-900/80 text-rose-800 dark:text-rose-300 text-sm">
					Failed to load domains: {error.message}
				</div>
			)}

			{data && (
				<DataTable
					columns={columns}
					data={data.domains}
					keyExtractor={(d) => d.slug}
					onRowClick={(d) => navigate(`/domains/${d.slug}`)}
					emptyMessage="No domains found."
				/>
			)}
		</div>
	);
};
