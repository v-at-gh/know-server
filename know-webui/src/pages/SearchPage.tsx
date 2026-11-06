import React, { useState, useEffect } from "react";
import { Link } from "react-router-dom";
import { useSearch } from "../api/queries";
import { ALL_LANGS } from "../lib/format";
import { SearchArticleItem, SearchQuestionItem } from "../api/types";

// src/pages/SearchPage.tsx
const Snippet: React.FC<{ html: string }> = ({ html }) => {
	if (!html) return null;

	const sanitizedHtml = html
		.replace(/<mark>/g, '<mark class="bg-amber-200 dark:bg-amber-900/60 px-0.5 rounded font-medium text-amber-950 dark:text-amber-100">')
		.replace(/<\/mark>/g, '</mark>');

	return (
		<span
			className="text-sm text-slate-600 dark:text-slate-300"
			dangerouslySetInnerHTML={{ __html: sanitizedHtml }}
		/>
	);
};

export const SearchPage: React.FC = () => {
	const [inputQuery, setInputQuery] = useState("");
	const [debouncedQuery, setDebouncedQuery] = useState("");
	const [selectedLang, setSelectedLang] = useState<string>("");
	const [selectedType, setSelectedType] = useState<string>("all");

	useEffect(() => {
		const timer = setTimeout(() => {
			setDebouncedQuery(inputQuery);
		}, 300);
		return () => clearTimeout(timer);
	}, [inputQuery]);

	const queryToSearch = debouncedQuery.trim().length >= 2 ? debouncedQuery.trim() : "";
	const { data, isLoading, error } = useSearch(queryToSearch, selectedLang || undefined, selectedType);

	const showArticles = selectedType === "all" || selectedType === "articles";
	const showQuestions = selectedType === "all" || selectedType === "questions";

	return (
		<div className="space-y-6">
			<div>
				<h2 className="text-2xl font-bold text-slate-900 dark:text-slate-100 tracking-tight">Search</h2>
				<p className="text-sm text-slate-600 dark:text-slate-400 mt-1">
					Keyword + semantic search across articles and questions.
				</p>
			</div>

			<div className="bg-white dark:bg-slate-900 p-4 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-4">
				<div className="relative">
					<input
						type="text"
						autoFocus
						value={inputQuery}
						onChange={(e) => setInputQuery(e.target.value)}
						placeholder="Search articles and questions..."
						className="w-full px-4 py-3 bg-slate-50 dark:bg-slate-800/80 border border-slate-300 dark:border-slate-700/80 rounded-lg text-base text-slate-900 dark:text-slate-100 placeholder-slate-400 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
					/>
					{inputQuery && (
						<button
							onClick={() => {
								setInputQuery("");
								setDebouncedQuery("");
							}}
							className="absolute right-3 top-1/2 -translate-y-1/2 text-xs font-semibold px-2 py-1 text-slate-500 hover:text-slate-700 dark:hover:text-slate-200 bg-slate-200 dark:bg-slate-700 rounded transition-colors"
						>
							Clear
						</button>
					)}
				</div>

				<div className="flex flex-wrap items-center justify-between gap-4 pt-1">
					{/* Type filter */}
					<div className="flex items-center gap-1 bg-slate-100 dark:bg-slate-800 p-1 rounded-lg border border-slate-200 dark:border-slate-700/60">
						{[
							{ id: "all", label: "All" },
							{ id: "articles", label: "Articles" },
							{ id: "questions", label: "Questions" },
						].map((t) => (
							<button
								key={t.id}
								onClick={() => setSelectedType(t.id)}
								className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${selectedType === t.id
									? "bg-white dark:bg-slate-900 text-indigo-600 dark:text-indigo-400 shadow-sm font-semibold"
									: "text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200"
									}`}
							>
								{t.label}
							</button>
						))}
					</div>

					{/* Language filter */}
					<div className="flex items-center gap-2">
						<label htmlFor="search-lang-select" className="text-xs font-semibold uppercase text-slate-500 tracking-wider">Language:</label>
						<select
							id="search-lang-select"
							value={selectedLang}
							onChange={(e) => setSelectedLang(e.target.value)}
							className="px-3 py-1.5 bg-slate-50 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg text-xs font-mono uppercase text-slate-800 dark:text-slate-200 focus:outline-none focus:border-indigo-500"
						>
							<option value="">All Languages</option>
							{ALL_LANGS.map((lang) => (
								<option key={lang} value={lang}>
									{lang}
								</option>
							))}
						</select>
					</div>
				</div>
			</div>

			{/* Semantic Search Status Banner */}
			{data && data.semantic_available === false && queryToSearch && (
				<div className="text-xs font-medium text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-900/60 px-3.5 py-2.5 rounded-lg flex items-center gap-2">
					<span className="inline-block w-2 h-2 rounded-full bg-amber-500" />
					Semantic search unavailable, showing keyword results only.
				</div>
			)}

			{/* Initial Empty State */}
			{!queryToSearch && !isLoading && (
				<div className="py-20 text-center text-slate-500 dark:text-slate-400">
					Type at least 2 characters to search.
				</div>
			)}

			{/* Loading State */}
			{isLoading && queryToSearch && (
				<div className="py-20 text-center text-slate-500 dark:text-slate-400 flex flex-col items-center justify-center gap-3">
					<div className="w-8 h-8 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
					Searching...
				</div>
			)}

			{/* Error State */}
			{error && queryToSearch && (
				<div className="p-4 rounded-lg bg-rose-100 dark:bg-rose-950/60 border border-rose-300 dark:border-rose-900/80 text-rose-800 dark:text-rose-300 text-sm">
					Search error: {error.message}
				</div>
			)}

			{/* Search Results */}
			{data && queryToSearch && !isLoading && (
				<div className="space-y-8">
					<div className="text-xs font-mono text-slate-500 dark:text-slate-400">
						Returned in {data.elapsed_ms}ms
					</div>

					{/* Articles Section */}
					{showArticles && (
						<div className="space-y-4">
							<h3 className="text-lg font-bold text-slate-900 dark:text-slate-100 border-b border-slate-200 dark:border-slate-800 pb-2">
								Articles ({data.articles.length})
							</h3>
							{data.articles.length === 0 ? (
								<p className="text-sm text-slate-500 dark:text-slate-400 italic">No articles found.</p>
							) : (
								<div className="space-y-3">
									{data.articles.map((art: SearchArticleItem) => (
										<div
											key={`${art.uuid}-${art.lang}`}
											className="p-4 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl hover:border-slate-300 dark:hover:border-slate-700 transition-colors shadow-sm space-y-2"
										>
											<div className="flex items-start justify-between gap-4">
												<Link
													to={`/articles/${art.lang}/${art.domain_slug}/${art.slug}`}
													className="font-semibold text-indigo-600 dark:text-indigo-400 hover:underline text-base"
												>
													{art.title || art.slug}
												</Link>
												<div className="flex items-center gap-2 text-xs font-mono text-slate-500 dark:text-slate-400 shrink-0">
													<span className="uppercase font-medium px-2 py-0.5 bg-slate-100 dark:bg-slate-800 rounded border border-slate-200 dark:border-slate-700">
														{art.domain_slug} · {art.lang}
													</span>
													{art.rrf_score > 0 && (
														<span className="px-1.5 py-0.5 bg-indigo-50 dark:bg-indigo-950/60 text-indigo-700 dark:text-indigo-300 rounded border border-indigo-200 dark:border-indigo-900/60">
															RRF: {art.rrf_score.toFixed(3)}
														</span>
													)}
												</div>
											</div>
											<div className="text-sm">
												<Snippet html={art.snippet || art.summary} />
											</div>
										</div>
									))}
								</div>
							)}
						</div>
					)}

					{/* Questions Section */}
					{showQuestions && (
						<div className="space-y-4">
							<h3 className="text-lg font-bold text-slate-900 dark:text-slate-100 border-b border-slate-200 dark:border-slate-800 pb-2">
								Questions ({data.questions.length})
							</h3>
							{data.questions.length === 0 ? (
								<p className="text-sm text-slate-500 dark:text-slate-400 italic">No questions found.</p>
							) : (
								<div className="space-y-3">
									{data.questions.map((q: SearchQuestionItem) => (
										<div
											key={`${q.uuid}-${q.lang}`}
											className="p-4 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl hover:border-slate-300 dark:hover:border-slate-700 transition-colors shadow-sm space-y-2"
										>
											<div className="flex items-start justify-between gap-4">
												<Link
													to={`/questions/${q.uuid}`}
													className="font-semibold text-indigo-600 dark:text-indigo-400 hover:underline text-base"
												>
													{q.text}
												</Link>
												<div className="flex items-center gap-2 text-xs font-mono text-slate-500 dark:text-slate-400 shrink-0">
													<span className="uppercase font-medium px-2 py-0.5 bg-slate-100 dark:bg-slate-800 rounded border border-slate-200 dark:border-slate-700">
														{q.domain_slug} · {q.lang}
													</span>
													{q.rrf_score > 0 && (
														<span className="px-1.5 py-0.5 bg-indigo-50 dark:bg-indigo-950/60 text-indigo-700 dark:text-indigo-300 rounded border border-indigo-200 dark:border-indigo-900/60">
															RRF: {q.rrf_score.toFixed(3)}
														</span>
													)}
												</div>
											</div>
											{q.answer && (
												<div className="text-xs font-mono text-slate-600 dark:text-slate-400">
													<span className="font-semibold text-slate-700 dark:text-slate-300">Answer:</span> {q.answer}
												</div>
											)}
											<div className="text-sm">
												<Snippet html={q.snippet} />
											</div>
										</div>
									))}
								</div>
							)}
						</div>
					)}
				</div>
			)}
		</div>
	);
};
