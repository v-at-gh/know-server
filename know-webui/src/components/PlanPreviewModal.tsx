import React from "react";
import { TranslatePlanResponse } from "../api/types";
import { StatusBadge } from "./StatusBadge";

interface PlanPreviewModalProps {
	isOpen: boolean;
	onClose: () => void;
	planData?: TranslatePlanResponse;
	isLoading: boolean;
	error?: Error | null;
	onConfirm: () => void;
	isApplying: boolean;
}

export const PlanPreviewModal: React.FC<PlanPreviewModalProps> = ({
	isOpen,
	onClose,
	planData,
	isLoading,
	error,
	onConfirm,
	isApplying,
}) => {
	if (!isOpen) return null;

	const isOrchestratorOffline = error && (error.message.includes("503") || error.message.includes("HTTP 503") || error.message.includes("Failed to fetch") || error.message.includes("NetworkError"));

	return (
		<div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
			<div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl max-w-2xl w-full max-h-[90vh] flex flex-col shadow-2xl overflow-hidden">
				<div className="flex items-center justify-between px-6 py-4 border-b border-slate-200 dark:border-slate-800">
					<h3 className="text-lg font-semibold text-slate-900 dark:text-slate-100">Translation Plan Preview</h3>
					<button
						onClick={onClose}
						className="text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200 p-1 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
					>
						✕
					</button>
				</div>

				<div className="p-6 overflow-y-auto flex-1 space-y-4">
					{isLoading && (
						<div className="py-12 text-center text-slate-500 dark:text-slate-400 flex flex-col items-center justify-center gap-3">
							<div className="w-6 h-6 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
							Generating translation plan...
						</div>
					)}

					{error && (
						<div className="p-4 rounded-lg bg-rose-100 dark:bg-rose-950/55 border border-rose-300 dark:border-rose-800/80 text-rose-800 dark:text-rose-300 text-sm space-y-2">
							<p className="font-semibold">Failed to load translation plan.</p>
							<p className="text-xs font-mono bg-rose-200 dark:bg-rose-900/40 p-2 rounded">{error.message}</p>
							{isOrchestratorOffline && (
								<p className="text-xs text-rose-700 dark:text-rose-200 mt-2">
									Orchestrator offline. Start with: <code className="bg-slate-200 dark:bg-slate-950 px-1 py-0.5 rounded text-emerald-700 dark:text-emerald-300">uv run --group web uvicorn orchestrator.app:app --port 8002</code>
								</p>
							)}
						</div>
					)}

					{planData && (
						<>
							<div className="grid grid-cols-3 gap-3">
								<div className="bg-slate-100 dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700/60 p-3 rounded-lg text-center">
									<div className="text-2xl font-bold text-emerald-600 dark:text-emerald-400">{planData.summary.queue}</div>
									<div className="text-xs text-slate-500 dark:text-slate-400 uppercase tracking-wider mt-1">To Queue</div>
								</div>
								<div className="bg-slate-100 dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700/60 p-3 rounded-lg text-center">
									<div className="text-2xl font-bold text-slate-700 dark:text-slate-300">{planData.summary.skip}</div>
									<div className="text-xs text-slate-500 dark:text-slate-400 uppercase tracking-wider mt-1">To Skip</div>
								</div>
								<div className="bg-slate-100 dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700/60 p-3 rounded-lg text-center">
									<div className="text-2xl font-bold text-rose-600 dark:text-rose-400">{planData.summary.blocked}</div>
									<div className="text-xs text-slate-500 dark:text-slate-400 uppercase tracking-wider mt-1">Blocked</div>
								</div>
							</div>

							<div className="space-y-2">
								<div className="text-xs uppercase font-semibold text-slate-500 dark:text-slate-400 tracking-wider">
									Matched Domains ({planData.domains_matched})
								</div>
								<div className="border border-slate-200 dark:border-slate-800 rounded-lg overflow-hidden max-h-64 overflow-y-auto">
									<table className="w-full text-left text-xs text-slate-700 dark:text-slate-300">
										<thead className="bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 uppercase sticky top-0">
											<tr>
												<th className="px-3 py-2">Slug</th>
												<th className="px-3 py-2">Lang</th>
												<th className="px-3 py-2">Action</th>
												<th className="px-3 py-2">State</th>
												<th className="px-3 py-2">Reason</th>
											</tr>
										</thead>
										<tbody className="divide-y divide-slate-200 dark:divide-slate-800">
											{planData.items.map((item, i) => (
												<tr key={i} className="hover:bg-slate-50 dark:hover:bg-slate-800/40">
													<td className="px-3 py-2 font-mono text-indigo-600 dark:text-indigo-300">{item.slug}</td>
													<td className="px-3 py-2 uppercase font-semibold">{item.lang}</td>
													<td className="px-3 py-2">
														<span
															className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${item.action === "queue"
																	? "bg-emerald-100 dark:bg-emerald-950 text-emerald-800 dark:text-emerald-400 border border-emerald-300 dark:border-emerald-800"
																	: "bg-slate-200 dark:bg-slate-800 text-slate-700 dark:text-slate-400 border border-slate-300 dark:border-slate-700"
																}`}
														>
															{item.action}
														</span>
													</td>
													<td className="px-3 py-2">
														<StatusBadge status={{ state: item.state }} compact />
													</td>
													<td className="px-3 py-2 text-slate-500 dark:text-slate-400">{item.reason}</td>
												</tr>
											))}
										</tbody>
									</table>
								</div>
							</div>
						</>
					)}
				</div>

				<div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-950/50">
					<button
						onClick={onClose}
						disabled={isApplying}
						className="px-4 py-2 rounded-lg text-sm font-medium text-slate-700 dark:text-slate-300 bg-slate-200 dark:bg-slate-800 hover:bg-slate-300 dark:hover:bg-slate-700 transition-colors disabled:opacity-50"
					>
						Cancel
					</button>
					{planData && planData.summary.queue > 0 && (
						<button
							onClick={onConfirm}
							disabled={isApplying}
							className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500 transition-colors disabled:opacity-50 flex items-center gap-2"
						>
							{isApplying && <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />}
							Apply & Enqueue ({planData.summary.queue})
						</button>
					)}
				</div>
			</div>
		</div>
	);
};
