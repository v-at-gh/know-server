import React, { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useCampaignDetail } from "../api/queries";
import { ALL_LANGS, DEFAULT_LANGS, cellColorClass } from "../lib/format";
import { CampaignCellEntry, CellStatus } from "../api/types";

export const CampaignDetailPage: React.FC = () => {
	const { slug } = useParams<{ slug: string }>();
	const navigate = useNavigate();
	const [selectedLangs, setSelectedLangs] = useState<string[]>(() => [...DEFAULT_LANGS]);

	const { data: detail, isLoading, error } = useCampaignDetail(slug, selectedLangs);

	const toggleLang = (lang: string) => {
		if (selectedLangs.includes(lang)) {
			if (selectedLangs.length > 1) {
				setSelectedLangs(selectedLangs.filter((l) => l !== lang));
			}
		} else {
			setSelectedLangs([...selectedLangs, lang]);
		}
	};

	if (isLoading) {
		return (
			<div className="py-20 text-center text-slate-500 dark:text-slate-400 flex flex-col items-center justify-center gap-3">
				<div className="w-8 h-8 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
				Loading campaign details...
			</div>
		);
	}

	if (error || !detail) {
		return (
			<div className="space-y-4">
				<div className="p-4 rounded-lg bg-rose-100 dark:bg-rose-950/60 border border-rose-300 dark:border-rose-900/80 text-rose-800 dark:text-rose-300 text-sm">
					{error ? `Error: ${error.message}` : `Campaign not found: ${slug}`}
				</div>
				<button
					onClick={() => navigate("/campaigns")}
					className="px-4 py-2 bg-slate-200 dark:bg-slate-800 hover:bg-slate-300 dark:hover:bg-slate-700 text-slate-800 dark:text-slate-200 rounded-lg text-sm font-medium transition-colors"
				>
					← Back to Campaigns
				</button>
			</div>
		);
	}

	const domains = detail.domain_slugs || [];
	const langs = detail.langs || selectedLangs;

	const cellMap = new Map<string, CampaignCellEntry>();
	for (const cell of detail.cells || []) {
		cellMap.set(`${cell.lang}:${cell.domain}`, cell);
	}

	return (
		<div className="space-y-6">
			{/* Header */}
			<div className="bg-white dark:bg-slate-900 p-6 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-4">
				<div>
					<button
						onClick={() => navigate("/campaigns")}
						className="text-xs font-medium text-indigo-600 dark:text-indigo-400 hover:text-indigo-500 dark:hover:text-indigo-300 mb-2 inline-block"
					>
						← Back to Campaigns
					</button>
					<h2 className="text-2xl font-bold text-slate-900 dark:text-slate-100">{detail.name}</h2>
					<p className="text-sm text-slate-600 dark:text-slate-400 mt-1">{detail.description}</p>
					<div className="flex flex-wrap items-center gap-3 mt-3">
						<span className="text-xs font-mono text-slate-500 dark:text-slate-400">Slug: {detail.slug}</span>
						<span className="text-xs font-mono px-2 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 border border-slate-300 dark:border-slate-700">
							Order: {detail.order}
						</span>
					</div>
				</div>

				{/* Prerequisites */}
				{(detail.prerequisite_domain_slugs?.length > 0 || detail.prerequisite_campaign_slugs?.length > 0) && (
					<div className="pt-3 border-t border-slate-200 dark:border-slate-800 flex flex-wrap gap-4 text-xs">
						{detail.prerequisite_domain_slugs?.length > 0 && (
							<div>
								<span className="font-semibold text-slate-500 dark:text-slate-400 mr-2">Prereq Domains:</span>
								{detail.prerequisite_domain_slugs.map((d) => (
									<span key={d} className="inline-block px-2 py-0.5 rounded bg-slate-100 dark:bg-slate-800 font-mono mr-1">
										{d}
									</span>
								))}
							</div>
						)}
						{detail.prerequisite_campaign_slugs?.length > 0 && (
							<div>
								<span className="font-semibold text-slate-500 dark:text-slate-400 mr-2">Prereq Campaigns:</span>
								{detail.prerequisite_campaign_slugs.map((c) => (
									<span key={c} className="inline-block px-2 py-0.5 rounded bg-indigo-50 dark:bg-indigo-950/50 text-indigo-700 dark:text-indigo-300 font-mono mr-1">
										{c}
									</span>
								))}
							</div>
						)}
					</div>
				)}
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
			<div className="overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-sm">
				<table className="w-full text-left text-sm text-slate-700 dark:text-slate-300">
					<thead className="bg-slate-100 dark:bg-slate-800/80 text-xs uppercase text-slate-600 dark:text-slate-400 border-b border-slate-200 dark:border-slate-800">
						<tr>
							<th className="px-4 py-3 font-semibold sticky left-0 bg-slate-100 dark:bg-slate-800 z-10">Domain</th>
							{langs.map((lang) => (
								<th key={lang} className="px-3 py-3 font-mono uppercase text-center font-semibold">
									{lang}
								</th>
							))}
						</tr>
					</thead>
					<tbody className="divide-y divide-slate-200 dark:divide-slate-800/60">
						{domains.map((domainSlug) => (
							<tr key={domainSlug} className="hover:bg-slate-50 dark:hover:bg-slate-800/40 transition-colors">
								<td className="px-4 py-3 font-medium text-slate-900 dark:text-slate-200 sticky left-0 bg-white dark:bg-slate-900 z-10">
									<button
										onClick={() => navigate(`/domains/${domainSlug}?lang=${selectedLangs[0] || "en"}`)}
										className="text-indigo-600 dark:text-indigo-400 hover:text-indigo-500 dark:hover:text-indigo-300 font-mono text-left block"
									>
										{domainSlug}
									</button>
								</td>
								{langs.map((lang) => {
									const cell = cellMap.get(`${lang}:${domainSlug}`);
									const status: CellStatus = cell ? cell.status : "unknown";
									const colorCls = cellColorClass(status);
									const displayText = cell && cell.questions > 0 ? cell.questions : "·";

									return (
										<td key={lang} className="px-3 py-3 text-center">
											<span
												onClick={() => navigate(`/domains/${domainSlug}?lang=${lang}`)}
												className={`inline-flex items-center justify-center w-8 h-8 rounded-lg text-xs font-semibold border cursor-pointer transition-transform hover:scale-105 ${colorCls}`}
												title={`${domainSlug} (${lang}): status=${status}, questions=${cell?.questions || 0}, articles=${cell?.articles || 0}/${cell?.expected_articles || 0}`}
											>
												{cell ? displayText : "—"}
											</span>
										</td>
									);
								})}
							</tr>
						))}
						{domains.length === 0 && (
							<tr>
								<td colSpan={1 + langs.length} className="py-12 text-center text-slate-500">
									No domains associated with this campaign.
								</td>
							</tr>
						)}
					</tbody>
				</table>
			</div>

			{/* Legend */}
			<div className="flex flex-wrap items-center gap-4 text-xs text-slate-600 dark:text-slate-400 bg-white dark:bg-slate-900 p-4 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm">
				<span className="font-semibold uppercase tracking-wider text-slate-500">Legend:</span>
				<div className="flex items-center gap-1.5"><span className="w-3 h-3 rounded bg-emerald-500 inline-block" /> ok</div>
				<div className="flex items-center gap-1.5"><span className="w-3 h-3 rounded bg-amber-500 inline-block" /> thin</div>
				<div className="flex items-center gap-1.5"><span className="w-3 h-3 rounded bg-rose-500 inline-block" /> incomplete</div>
				<div className="flex items-center gap-1.5"><span className="w-3 h-3 rounded bg-slate-500 inline-block" /> absent</div>
				<div className="flex items-center gap-1.5"><span className="w-3 h-3 rounded bg-slate-300 dark:bg-slate-700 inline-block" /> unknown</div>
			</div>
		</div>
	);
};
