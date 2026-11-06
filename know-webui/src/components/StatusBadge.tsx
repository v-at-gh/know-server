import React from "react";
import { DomainStatus, DomainState } from "../api/types";
import { stateColorClass } from "../lib/format";

interface StatusBadgeProps {
	status?: DomainStatus | { state: DomainState | string };
	state?: string;
	onClick?: () => void;
	compact?: boolean;
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({ status, state: directState, onClick, compact = false }) => {
	const rawState = directState || status?.state || "missing";

	let colorClass = stateColorClass(rawState as DomainState);
	if (rawState === "ready") {
		colorClass = "bg-emerald-950/60 text-emerald-400 border-emerald-800/60";
	} else if (rawState === "short") {
		colorClass = "bg-amber-950/60 text-amber-400 border-amber-800/60";
	} else if (rawState === "broken") {
		colorClass = "bg-rose-950/60 text-rose-400 border-rose-800/60";
	} else if (rawState === "archived") {
		colorClass = "bg-purple-950/60 text-purple-400 border-purple-800/60";
	}

	if (compact) {
		let label = "—";
		if (rawState === "bootstrapped" || rawState === "ready") label = "✓";
		else if (rawState === "partial" || rawState === "short") label = "◐";
		else if (rawState === "broken" || rawState === "absent_source") label = "×";

		return (
			<span
				onClick={onClick}
				className={`inline-flex items-center justify-center w-7 h-7 rounded text-xs font-semibold border cursor-pointer transition-colors hover:opacity-80 ${colorClass}`}
				title={`${rawState}${status && 'questions' in status ? ` (${status.questions}q)` : ''}`}
			>
				{label}
			</span>
		);
	}

	let text: string = rawState;
	if (status && "questions" in status && "articles" in status) {
		if (rawState === "bootstrapped") {
			text = `${status.questions} q`;
		} else if (rawState === "partial") {
			text = `${status.questions}/${status.expected_questions} q`;
		} else if (rawState === "absent_source") {
			text = "no source";
		} else {
			text = "missing";
		}
	}

	return (
		<span
			onClick={onClick}
			className={`inline-flex items-center px-2.5 py-1 rounded-md text-xs font-medium border ${colorClass} ${onClick ? 'cursor-pointer hover:opacity-80' : ''}`}
		>
			{text}
		</span>
	);
};
