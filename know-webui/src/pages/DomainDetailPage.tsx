import React, { useState } from "react";
import { useParams, useSearchParams, useNavigate } from "react-router-dom";
import {
	useDomainDetail,
	useTranslatePlan,
	useTranslateEnqueue,
	useDomainArticles,
	useDomainQuestions,
} from "../api/queries";
import { ALL_LANGS } from "../lib/format";
import { StatusBadge } from "../components/StatusBadge";
import { PlanPreviewModal } from "../components/PlanPreviewModal";

export const DomainDetailPage: React.FC = () => {
	const { slug } = useParams<{ slug: string }>();
	const [searchParams] = useSearchParams();
	const navigate = useNavigate();
	const preLang = searchParams.get("lang") || "ru";

	const [targetLangs, setTargetLangs] = useState<string[]>([preLang]);
	const [selectedLang, setSelectedLang] = useState<string>(preLang);
	const [showModal, setShowModal] = useState(false);
	const [toastMessage, setToastMessage] = useState<string | null>(null);

	// Filters for granular units
	const [articleStatus, setArticleStatus] = useState<string>("");
	const [questionStatus, setQuestionStatus] = useState<string>("");
	const [questionArticleSlug, setQuestionArticleSlug] = useState<string>("");

	const { data: domain, isLoading, error } = useDomainDetail(slug, targetLangs);

	// Granular queries
	const articlesQuery = useDomainArticles(slug, selectedLang, articleStatus || undefined);
	const questionsQuery = useDomainQuestions(
		slug,
		selectedLang,
		questionArticleSlug || undefined,
		questionStatus || undefined
	);

	// Plan query when modal is open
	const planQuery = useTranslatePlan({ slug, langs: targetLangs }, showModal);
	const enqueueMutation = useTranslateEnqueue();

	const toggleTargetLang = (lang: string) => {
		if (targetLangs.includes(lang)) {
			if (targetLangs.length > 1) {
				setTargetLangs(targetLangs.filter((l) => l !== lang));
			}
		} else {
			setTargetLangs([...targetLangs, lang]);
		}
	};

	const handlePreviewPlan = () => {
		setShowModal(true);
	};

	const handleConfirmEnqueue = () => {
		enqueueMutation.mutate(
			{ slug, langs: targetLangs },
			{
				onSuccess: () => {
					setShowModal(false);
					setToastMessage(`Successfully enqueued translation tasks.`);
					setTimeout(() => setToastMessage(null), 4000);
				},
			}
		);
	};

	if (isLoading) {
		return (
			<div className="py-20 text-center text-slate-500 dark:text-slate-400 flex flex-col items-center justify-center gap-3">
				<div className="w-8 h-8 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
				Loading domain details...
			</div>
		);
	}

	if (error || !domain) {
		return (
			<div className="space-y-4">
				<div className="p-4 rounded-lg bg-rose-100 dark:bg-rose-950/60 border border-rose-300 dark:border-rose-900/80 text-rose-800 dark:text-rose-300 text-sm">
					{error ? `Error: ${error.message}` : `Domain not found: ${slug}`}
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

	return (
		<div className="space-y-6">
			{toastMessage && (
				<div className="p-4 rounded-lg bg-emerald-100 dark:bg-emerald-950/80 border border-emerald-300 dark:border-emerald-800 text-emerald-800 dark:text-emerald-300 text-sm shadow-xl flex items-center justify-between">
					<span>{toastMessage}</span>
					<button onClick={() => setToastMessage(null)} className="text-emerald-700 dark:text-emerald-400 hover:text-emerald-900 dark:hover:text-emerald-200">✕</button>
				</div>
			)}

			{/* Header */}
			<div className="flex flex-col md:flex-row md:items-center justify-between gap-4 bg-white dark:bg-slate-900 p-6 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-2">
				<div>
					<button
						onClick={() => navigate("/domains")}
						className="text-xs font-medium text-indigo-600 dark:text-indigo-400 hover:text-indigo-500 dark:hover:text-indigo-300 mb-2 inline-block"
					>
						← Back to Domains
					</button>
					<h2 className="text-2xl font-bold text-slate-900 dark:text-slate-100">{domain.name}</h2>
					{domain.description && <p className="text-sm text-slate-600 dark:text-slate-400 mt-1">{domain.description}</p>}
					<div className="flex flex-wrap items-center gap-3 mt-2">
						<span className="text-xs font-mono text-slate-500 dark:text-slate-400">Slug: {domain.slug}</span>
						<span className="text-xs font-mono px-2 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 border border-slate-300 dark:border-slate-700">
							{domain.kind}
						</span>
						{domain.campaigns?.length > 0 && (
							<div className="flex items-center gap-1">
								<span className="text-xs text-slate-500">Campaigns:</span>
								{domain.campaigns.map((c) => (
									<button
										key={c}
										onClick={() => navigate(`/campaigns/${c}`)}
										className="text-xs font-mono px-2 py-0.5 rounded bg-indigo-50 dark:bg-indigo-950/60 text-indigo-700 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-900 hover:underline"
									>
										{c}
									</button>
								))}
							</div>
						)}
					</div>
				</div>
			</div>

			{/* Translate Action Section */}
			<div className="bg-white dark:bg-slate-900 p-6 rounded-xl border border-slate-200 dark:border-slate-800 space-y-4 shadow-sm">
				<h3 className="text-base font-semibold text-slate-900 dark:text-slate-100">Trigger Translation</h3>
				<p className="text-xs text-slate-600 dark:text-slate-400">Select target languages to translate this domain, preview the plan, and enqueue orchestration tasks.</p>

				<div className="flex flex-wrap gap-1.5 items-center pt-2">
					{ALL_LANGS.map((lang) => {
						const isSelected = targetLangs.includes(lang);
						return (
							<button
								key={lang}
								onClick={() => toggleTargetLang(lang)}
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

				<div className="pt-2 flex justify-end">
					<button
						onClick={handlePreviewPlan}
						disabled={targetLangs.length === 0}
						className="px-5 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium rounded-lg transition-colors shadow-lg shadow-indigo-600/20 disabled:opacity-50"
					>
						Preview Translation Plan
					</button>
				</div>
			</div>

			{/* Language Status Cards Grid */}
			<div className="space-y-3">
				<h3 className="text-base font-semibold text-slate-900 dark:text-slate-100">Translation Status by Language</h3>
				<div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
					{Object.entries(domain.status || {}).map(([lang, st]) => (
						<div key={lang} className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl p-5 space-y-3 shadow-sm">
							<div className="flex items-center justify-between">
								<span className="font-mono text-base font-bold uppercase text-slate-800 dark:text-slate-200">{lang}</span>
								<StatusBadge status={st} />
							</div>
							<div className="grid grid-cols-2 gap-2 pt-2 text-xs text-slate-600 dark:text-slate-400 border-t border-slate-200 dark:border-slate-800">
								<div>
									<span className="block text-slate-500 uppercase text-[10px]">Articles</span>
									<span className="font-medium text-slate-800 dark:text-slate-200">{st.articles} / {st.expected_articles}</span>
								</div>
								<div>
									<span className="block text-slate-500 uppercase text-[10px]">Questions</span>
									<span className="font-medium text-slate-800 dark:text-slate-200">{st.questions} / {st.expected_questions}</span>
								</div>
							</div>
							{st.missing_uuids !== undefined && st.missing_uuids > 0 && (
								<div className="text-[11px] text-amber-700 dark:text-amber-400 bg-amber-100 dark:bg-amber-950/40 px-2 py-1 rounded border border-amber-300 dark:border-amber-900/60">
									Missing UUIDs: {st.missing_uuids}
								</div>
							)}
						</div>
					))}
				</div>
			</div>

			{/* Granular Units Drilldown Section */}
			<div className="bg-white dark:bg-slate-900 p-6 rounded-xl border border-slate-200 dark:border-slate-800 space-y-6 shadow-sm">
				<div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-slate-200 dark:border-slate-800 pb-4">
					<div>
						<h3 className="text-lg font-bold text-slate-900 dark:text-slate-100">Granular Units Inspection</h3>
						<p className="text-xs text-slate-600 dark:text-slate-400">Inspect articles and questions breakdown for the selected language.</p>
					</div>
					<div className="flex items-center gap-2">
						<span className="text-xs font-mono text-slate-600 dark:text-slate-400">Language:</span>
						<select
							value={selectedLang}
							onChange={(e) => setSelectedLang(e.target.value)}
							className="bg-slate-100 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 text-slate-900 dark:text-slate-200 rounded-lg px-3 py-1.5 text-xs font-mono uppercase focus:outline-none focus:border-indigo-500"
						>
							{Object.keys(domain.status || {}).map((l) => (
								<option key={l} value={l}>
									{l}
								</option>
							))}
						</select>
					</div>
				</div>

				{/* Articles Table Card */}
				<div className="space-y-4">
					<div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
						<h4 className="text-sm font-semibold text-slate-900 dark:text-slate-200">
							Articles ({articlesQuery.data?.total || 0})
						</h4>
						<div className="flex items-center gap-2">
							<span className="text-xs text-slate-600 dark:text-slate-400">Status:</span>
							<select
								value={articleStatus}
								onChange={(e) => setArticleStatus(e.target.value)}
								className="bg-slate-100 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 text-slate-900 dark:text-slate-200 rounded px-2 py-1 text-xs"
							>
								<option value="">All</option>
								<option value="ready">Ready</option>
								<option value="short">Short</option>
								<option value="missing">Missing</option>
							</select>
						</div>
					</div>

					{articlesQuery.isLoading ? (
						<div className="py-8 text-center text-xs text-slate-500">Loading articles...</div>
					) : articlesQuery.error ? (
						<div className="p-3 text-xs text-rose-700 dark:text-rose-400 bg-rose-100 dark:bg-rose-950/40 rounded border border-rose-300 dark:border-rose-900/60">
							Failed to load articles: {articlesQuery.error.message}
						</div>
					) : (
						<div className="overflow-x-auto border border-slate-200 dark:border-slate-800 rounded-lg">
							<table className="w-full text-left text-xs">
								<thead className="bg-slate-100 dark:bg-slate-800/60 text-slate-600 dark:text-slate-400 font-mono uppercase text-[10px] border-b border-slate-200 dark:border-slate-800">
									<tr>
										<th className="p-3">Order</th>
										<th className="p-3">Slug</th>
										<th className="p-3">Title</th>
										<th className="p-3">Bytes</th>
										<th className="p-3">Questions</th>
										<th className="p-3">State</th>
									</tr>
								</thead>
								<tbody className="divide-y divide-slate-200 dark:divide-slate-800/60 text-slate-700 dark:text-slate-300">
									{articlesQuery.data?.articles?.map((art) => (
										<tr
											key={art.slug}
											onClick={() => navigate(`/articles/${selectedLang}/${slug}/${art.slug}`)}
											className="hover:bg-slate-50 dark:hover:bg-slate-800/40 transition-colors cursor-pointer"
										>
											<td className="p-3 font-mono text-slate-500">{art.order}</td>
											<td className="p-3 font-mono text-indigo-600 dark:text-indigo-400 font-medium">{art.slug}</td>
											<td className="p-3 font-medium text-slate-900 dark:text-slate-200">{art.title}</td>
											<td className="p-3 font-mono text-slate-600 dark:text-slate-400">{art.bytes} B</td>
											<td className="p-3 font-mono text-slate-600 dark:text-slate-400">{art.questions_count}</td>
											<td className="p-3">
												<StatusBadge state={art.state} />
											</td>
										</tr>
									))}
									{(!articlesQuery.data?.articles || articlesQuery.data.articles.length === 0) && (
										<tr>
											<td colSpan={6} className="p-6 text-center text-slate-500">
												No articles found.
											</td>
										</tr>
									)}
								</tbody>
							</table>
						</div>
					)}
				</div>

				{/* Questions Table Card */}
				<div className="space-y-4 pt-4 border-t border-slate-200 dark:border-slate-800">
					<div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
						<h4 className="text-sm font-semibold text-slate-900 dark:text-slate-200">
							Questions ({questionsQuery.data?.total || 0})
						</h4>
						<div className="flex flex-wrap items-center gap-2">
							<select
								value={questionArticleSlug}
								onChange={(e) => setQuestionArticleSlug(e.target.value)}
								className="bg-slate-100 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 text-slate-900 dark:text-slate-200 rounded px-2 py-1 text-xs font-mono"
							>
								<option value="">All Articles</option>
								{articlesQuery.data?.articles?.map((a) => (
									<option key={a.slug} value={a.slug}>
										{a.slug}
									</option>
								))}
							</select>
							<select
								value={questionStatus}
								onChange={(e) => setQuestionStatus(e.target.value)}
								className="bg-slate-100 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 text-slate-900 dark:text-slate-200 rounded px-2 py-1 text-xs"
							>
								<option value="">All Statuses</option>
								<option value="ready">Ready</option>
								<option value="broken">Broken</option>
								<option value="missing">Missing</option>
								<option value="archived">Archived</option>
							</select>
						</div>
					</div>

					{questionsQuery.isLoading ? (
						<div className="py-8 text-center text-xs text-slate-500">Loading questions...</div>
					) : questionsQuery.error ? (
						<div className="p-3 text-xs text-rose-700 dark:text-rose-400 bg-rose-100 dark:bg-rose-950/40 rounded border border-rose-300 dark:border-rose-900/60">
							Failed to load questions: {questionsQuery.error.message}
						</div>
					) : (
						<div className="overflow-x-auto border border-slate-200 dark:border-slate-800 rounded-lg">
							<table className="w-full text-left text-xs">
								<thead className="bg-slate-100 dark:bg-slate-800/60 text-slate-600 dark:text-slate-400 font-mono uppercase text-[10px] border-b border-slate-200 dark:border-slate-800">
									<tr>
										<th className="p-3">UUID</th>
										<th className="p-3">Text</th>
										<th className="p-3">Type</th>
										<th className="p-3">Diff</th>
										<th className="p-3">Article</th>
										<th className="p-3">State</th>
									</tr>
								</thead>
								<tbody className="divide-y divide-slate-200 dark:divide-slate-800/60 text-slate-700 dark:text-slate-300">
									{questionsQuery.data?.questions?.map((q) => (
										<tr
											key={q.uuid}
											onClick={() => navigate(`/questions/${q.uuid}`)}
											className="hover:bg-slate-50 dark:hover:bg-slate-800/40 transition-colors cursor-pointer"
										>
											<td className="p-3 font-mono text-slate-500 dark:text-slate-400" title={q.uuid}>
												{q.uuid.slice(0, 8)}...
											</td>
											<td className="p-3 max-w-xs truncate" title={q.text}>
												{q.text}
											</td>
											<td className="p-3 font-mono text-slate-500 dark:text-slate-400">{q.type}</td>
											<td className="p-3 font-mono text-slate-500 dark:text-slate-400">{q.difficulty}</td>
											<td className="p-3 font-mono text-indigo-600 dark:text-indigo-400">{q.related_article_slug || "—"}</td>
											<td className="p-3">
												<StatusBadge state={q.state} />
											</td>
										</tr>
									))}
									{(!questionsQuery.data?.questions || questionsQuery.data.questions.length === 0) && (
										<tr>
											<td colSpan={6} className="p-6 text-center text-slate-500">
												No questions found.
											</td>
										</tr>
									)}
								</tbody>
							</table>
						</div>
					)}
				</div>
			</div>

			{/* Plan PreviewModal */}
			<PlanPreviewModal
				isOpen={showModal}
				onClose={() => setShowModal(false)}
				planData={planQuery.data}
				isLoading={planQuery.isLoading}
				error={planQuery.error}
				onConfirm={handleConfirmEnqueue}
				isApplying={enqueueMutation.isPending}
			/>
		</div>
	);
};
