import React from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { useArticleDetail } from "../api/queries";
import { StatusBadge } from "../components/StatusBadge";

export const ArticleDetailPage: React.FC = () => {
	const { lang, domain, slug } = useParams<{ lang: string; domain: string; slug: string }>();
	const navigate = useNavigate();

	const { data: art, isLoading, error } = useArticleDetail(lang, domain, slug);

	if (isLoading) {
		return (
			<div className="py-20 text-center text-slate-500 dark:text-slate-400 flex flex-col items-center justify-center gap-3">
				<div className="w-8 h-8 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
				Loading article content...
			</div>
		);
	}

	if (error || !art) {
		return (
			<div className="space-y-4">
				<div className="p-4 rounded-lg bg-rose-100 dark:bg-rose-950/60 border border-rose-300 dark:border-rose-900/80 text-rose-800 dark:text-rose-300 text-sm">
					{error ? `Error: ${error.message}` : `Article not found: ${domain}/${slug} (${lang})`}
				</div>
				<button
					onClick={() => navigate(`/domains/${domain}?lang=${lang}`)}
					className="px-4 py-2 bg-slate-200 dark:bg-slate-800 hover:bg-slate-300 dark:hover:bg-slate-700 text-slate-800 dark:text-slate-200 rounded-lg text-sm font-medium transition-colors"
				>
					← Back to Domain
				</button>
			</div>
		);
	}

	return (
		<div className="space-y-6">
			{/* Header */}
			<div className="bg-white dark:bg-slate-900 p-6 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-4">
				<button
					onClick={() => navigate(`/domains/${domain}?lang=${lang}`)}
					className="text-xs font-medium text-indigo-600 dark:text-indigo-400 hover:text-indigo-500 dark:hover:text-indigo-300 inline-block"
				>
					← Back to Domain ({domain} / {lang})
				</button>

				<div>
					<h2 className="text-2xl font-bold text-slate-900 dark:text-slate-100">{art.title}</h2>
					{art.summary && <p className="text-sm text-slate-600 dark:text-slate-400 mt-1">{art.summary}</p>}
				</div>

				<div className="flex flex-wrap items-center gap-3 pt-2">
					<span className="text-xs font-mono px-2.5 py-1 rounded-md bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 border border-slate-300 dark:border-slate-700">
						{art.md_bytes} bytes
					</span>
					<span className={`text-xs px-2.5 py-1 rounded-md border font-medium ${art.exists ? 'bg-emerald-100 dark:bg-emerald-950/60 text-emerald-800 dark:text-emerald-400 border-emerald-300 dark:border-emerald-800' : 'bg-rose-100 dark:bg-rose-950/60 text-rose-800 dark:text-rose-400 border-rose-300 dark:border-rose-900'}`}>
						{art.exists ? "Exists" : "Missing"}
					</span>
					{art.short && (
						<span className="text-xs px-2.5 py-1 rounded-md bg-amber-100 dark:bg-amber-950/60 text-amber-800 dark:text-amber-400 border border-amber-300 dark:border-amber-800 font-medium">
							Short Article
						</span>
					)}
				</div>
			</div>

			{/* Markdown Body (raw pre) */}
			<div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm p-6 space-y-3">
				<h3 className="text-sm font-semibold text-slate-900 dark:text-slate-200 uppercase tracking-wider">Markdown Content</h3>
				<pre className="whitespace-pre-wrap font-mono text-xs bg-slate-100 dark:bg-slate-950 text-slate-900 dark:text-slate-200 border border-slate-300 dark:border-slate-800 rounded-lg p-4 overflow-x-auto max-h-[600px] overflow-y-auto leading-relaxed">
					{art.text || "(empty markdown content)"}
				</pre>
			</div>

			{/* Questions Section */}
			<div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm p-6 space-y-4">
				<h3 className="text-sm font-semibold text-slate-900 dark:text-slate-200 uppercase tracking-wider">
					Questions ({art.questions?.length || 0})
				</h3>

				{(!art.questions || art.questions.length === 0) ? (
					<div className="p-8 text-center text-slate-500 dark:text-slate-400 text-xs bg-slate-50 dark:bg-slate-900/50 rounded-lg border border-slate-200 dark:border-slate-800">
						No questions for this article.
					</div>
				) : (
					<div className="space-y-3">
						{art.questions.map((q) => (
							<div key={q.uuid} className="p-4 rounded-lg bg-slate-50 dark:bg-slate-800/40 border border-slate-200 dark:border-slate-700/60 space-y-2">
								<div className="flex items-center justify-between">
									<Link
										to={`/questions/${q.uuid}`}
										className="font-mono text-xs text-indigo-600 dark:text-indigo-400 hover:underline"
									>
										UUID: {q.uuid}
									</Link>
									<div className="flex items-center gap-2">
										<span className="text-xs font-mono px-2 py-0.5 rounded bg-slate-200 dark:bg-slate-700 text-slate-700 dark:text-slate-300">
											Diff: {q.difficulty}
										</span>
										{q.archived && <StatusBadge state="archived" />}
									</div>
								</div>
								<p className="text-sm font-medium text-slate-900 dark:text-slate-200">{q.text}</p>
								<div className="text-xs text-slate-600 dark:text-slate-400 space-y-1 pt-1 border-t border-slate-200 dark:border-slate-700/60">
									<div><span className="font-semibold">Answer:</span> <span className="font-mono text-emerald-600 dark:text-emerald-400">{q.answer}</span></div>
									{q.distractors?.length > 0 && (
										<div>
											<span className="font-semibold">Distractors:</span>
											<ul className="list-disc list-inside pl-2 space-y-0.5 mt-0.5">
												{q.distractors.map((d, i) => (
													<li key={i} className="text-slate-600 dark:text-slate-400">
														{d.text} {d.why && <span className="text-slate-400 dark:text-slate-500 text-[11px]">({d.why})</span>}
													</li>
												))}
											</ul>
										</div>
									)}
								</div>
							</div>
						))}
					</div>
				)}
			</div>
		</div>
	);
};
