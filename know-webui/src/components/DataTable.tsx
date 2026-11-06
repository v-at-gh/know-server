import React from "react";

interface Column<T> {
	header: string;
	accessor: (item: T) => React.ReactNode;
	className?: string;
}

interface DataTableProps<T> {
	columns: Column<T>[];
	data: T[];
	keyExtractor: (item: T) => string;
	onRowClick?: (item: T) => void;
	emptyMessage?: string;
}

export function DataTable<T>({
	columns,
	data,
	keyExtractor,
	onRowClick,
	emptyMessage = "No items found.",
}: DataTableProps<T>) {
	if (!data || data.length === 0) {
		return (
			<div className="p-8 text-center text-slate-500 dark:text-slate-400 bg-slate-50 dark:bg-slate-900/50 rounded-lg border border-slate-200 dark:border-slate-800">
				{emptyMessage}
			</div>
		);
	}

	return (
		<div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900">
			<table className="w-full text-left text-sm text-slate-700 dark:text-slate-300">
				<thead className="bg-slate-100 dark:bg-slate-800/80 text-xs uppercase text-slate-600 dark:text-slate-400 border-b border-slate-200 dark:border-slate-700/60">
					<tr>
						{columns.map((col, idx) => (
							<th key={idx} scope="col" className={`px-4 py-3 font-semibold ${col.className || ""}`}>
								{col.header}
							</th>
						))}
					</tr>
				</thead>
				<tbody className="divide-y divide-slate-200 dark:divide-slate-800/60">
					{data.map((item) => (
						<tr
							key={keyExtractor(item)}
							onClick={() => onRowClick?.(item)}
							className={`transition-colors ${onRowClick ? "cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800/50" : ""
								}`}
						>
							{columns.map((col, idx) => (
								<td key={idx} className={`px-4 py-3 ${col.className || ""}`}>
									{col.accessor(item)}
								</td>
							))}
						</tr>
					))}
				</tbody>
			</table>
		</div>
	);
}
