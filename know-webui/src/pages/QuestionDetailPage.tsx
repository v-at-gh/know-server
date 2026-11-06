import React from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { useQuestionDetail } from "../api/queries";
import { StatusBadge } from "../components/StatusBadge";
import { QuestionAcrossLangView } from "../api/types";

export const QuestionDetailPage: React.FC = () => {
	const { uuid } = useParams<{ uuid: string }>();
	const navigate = useNavigate();

	const { data: qDetail, isLoading, error } = useQuestionDetail(uuid);

	if (isLoading) {
		return (
			<div className="py-20 text-center text-slate-500 dark:text-slate-400 flex flex-col items-center justify-center gap-3">
				<div className="w-8 h-8 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
				Loading question details...
			</div>
		);
	}

	if (error || !qDetail) {
		return (
			<div className="space-y-4">
				<div className="p-4 rounded-lg bg-rose-100 dark:bg-rose-950/60 border border-rose-300 dark:border-rose-900/80 text-rose-800 dark:text-rose-300 text-sm">
					{error ? `Error: ${error.message}` : `Question not found: ${uuid}`}
				</div>
				<button
					onClick={() => navigate("/domains")}
					className="px-4 py-2 bg-slate-200 dark:bg-slate-800 hover:bg-slate-300 dark:hover:bg-slate-700 text-slate-800 dark:text-slate-200 rounded-lg text-sm font-medium transition-colors"
				>
					← Back to Domains
				</button>
			</div>
		);
	}

	const domainSlug = qDetail.domain_slug;
	const backPath = domainSlug ? `/domains/${domainSlug}` : "/domains";

	// Parse article slug e.g. "domain/article-slug"
	let articleLink: React.ReactNode = null;
	if (qDetail.article_slug) {
		const parts = qDetail.article_slug.split("/");
		if (parts.length >= 2) {
			const artDomain = parts[0];
			const artSlug = parts.slice(1).join("/");
			articleLink = (
				<Link
					to={`/articles/en/${artDomain}/${artSlug}`}
					className="text-xs font-mono text-indigo-600 dark:text-indigo-400 hover:underline px-2.5 py-1 rounded bg-slate-100 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 inline-block"
				>
					Article: {qDetail.article_slug}
				</Link>
			);
		} else {
			articleLink = (
				<span className="text-xs font-mono text-slate-500 px-2.5 py-1 rounded bg-slate-100 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 inline-block">
					Article: {qDetail.article_slug}
				</span>
			);
		}
	}

	const enEntry = qDetail.languages?.find((l) => l.lang === "en" && l.present);

	return (
		<div className="space-y-6">
			{/* Header */}
			<div className="bg-white dark:bg-slate-900 p-6 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-4">
				<button
					onClick={() => navigate(backPath)}
					className="text-xs font-medium text-indigo-600 dark:text-indigo-400 hover:text-indigo-500 dark:hover:text-indigo-300 inline-block"
				>
					← Back to Domain {domainSlug ? `(${domainSlug})` : ""}
				</button>

				<div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
					<div>
						<h2 className="text-lg font-bold font-mono text-slate-900 dark:text-slate-100 break-all">{qDetail.uuid}</h2>
						<div className="flex flex-wrap items-center gap-2 mt-2">
							{domainSlug && (
								<span className="text-xs font-mono px-2 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 border border-slate-300 dark:border-slate-700">
									Domain: {domainSlug}
								</span>
							)}
							{articleLink}
							{qDetail.archived && <StatusBadge state="archived" />}
						</div>
					</div>
				</div>
			</div>

			{/* Language Grid */}
			<div className="space-y-4">
				<h3 className="text-base font-semibold text-slate-900 dark:text-slate-100">Translations Across Languages</h3>
				<div className="grid grid-cols-1 md:grid-cols-2 gap-4">
					{qDetail.languages?.map((langView: QuestionAcrossLangView) => {
						// Compute mismatch badges if lang !== "en" and present
						let mismatches: string[] = [];
						if (langView.lang !== "en" && langView.present && enEntry) {
							const distDelta = (langView.distractors?.length || 0) - (enEntry.distractors?.length || 0);
							if (distDelta !== 0) {
								mismatches.push(`D ${distDelta > 0 ? `+${distDelta}` : distDelta}`);
							}
							const hintDelta = (langView.hints?.length || 0) - (enEntry.hints?.length || 0);
							if (hintDelta !== 0) {
								mismatches.push(`H ${hintDelta > 0 ? `+${hintDelta}` : hintDelta}`);
							}
							if (langView.answer && enEntry.answer && langView.answer.trim() === enEntry.answer.trim()) {
								mismatches.push("answer not translated");
							}
						}

						return (
							<div key={langView.lang} className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl p-5 space-y-3 shadow-sm flex flex-col justify-between">
								<div className="space-y-3">
									<div className="flex items-center justify-between border-b border-slate-200 dark:border-slate-800 pb-2">
										<span className="font-mono text-base font-bold uppercase text-slate-900 dark:text-slate-100">
											{langView.lang}
										</span>
										<div className="flex items-center gap-2">
											{langView.present ? (
												<span className="px-2 py-0.5 rounded text-[10px] font-bold uppercase bg-emerald-100 dark:bg-emerald-950 text-emerald-800 dark:text-emerald-400 border border-emerald-300 dark:border-emerald-800">
													Present
												</span>
											) : (
												<span className="px-2 py-0.5 rounded text-[10px] font-bold uppercase bg-slate-200 dark:bg-slate-800 text-slate-600 dark:text-slate-400 border border-slate-300 dark:border-slate-700">
													Not Present
												</span>
											)}
										</div>
									</div>

									{/* Mismatch badge row */}
									{mismatches.length > 0 && (
										<div className="flex flex-wrap gap-1.5">
											{mismatches.map((m, idx) => (
												<span key={idx} className="px-2 py-0.5 rounded text-[10px] font-bold uppercase bg-amber-100 dark:bg-amber-950/60 text-amber-800 dark:text-amber-400 border border-amber-300 dark:border-amber-800/80">
													{m}
												</span>
											))}
										</div>
									)}

									{langView.present ? (
										<div className="space-y-3 pt-1 text-xs text-slate-700 dark:text-slate-300">
											<div>
												<span className="block text-slate-500 dark:text-slate-400 uppercase text-[10px] font-semibold">Question Text</span>
												<p className="font-medium text-slate-900 dark:text-slate-100 text-sm mt-0.5">{langView.text}</p>
											</div>

											<div>
												<span className="block text-slate-500 dark:text-slate-400 uppercase text-[10px] font-semibold">Answer</span>
												<p className="font-mono text-emerald-700 dark:text-emerald-400 font-medium mt-0.5">{langView.answer}</p>
											</div>

											{langView.distractors?.length > 0 && (
												<div>
													<span className="block text-slate-500 dark:text-slate-400 uppercase text-[10px] font-semibold">Distractors</span>
													<ul className="list-disc list-inside pl-2 space-y-0.5 mt-0.5">
														{langView.distractors.map((d, i) => (
															<li key={i} className="text-slate-700 dark:text-slate-300">
																{d.text} {d.why && <span className="text-slate-500 dark:text-slate-400 text-[11px]">({d.why})</span>}
															</li>
														))}
													</ul>
												</div>
											)}

											{langView.hints?.length > 0 && (
												<div>
													<span className="block text-slate-500 dark:text-slate-400 uppercase text-[10px] font-semibold">Hints</span>
													<ul className="list-disc list-inside pl-2 space-y-0.5 mt-0.5">
														{langView.hints.map((h, i) => (
															<li key={i} className="text-slate-700 dark:text-slate-300">{h}</li>
														))}
													</ul>
												</div>
											)}
										</div>
									) : (
										<div className="py-8 text-center text-slate-500 dark:text-slate-400 text-xs italic">
											Question translation not present for {langView.lang}.
										</div>
									)}
								</div>

								{langView.present && (
									<div className="pt-3 border-t border-slate-200 dark:border-slate-800 flex items-center justify-between text-xs text-slate-500">
										<span>Difficulty: {langView.difficulty}</span>
									</div>
								)}
							</div>
						);
					})}
				</div>
			</div>
		</div>
	);
};
