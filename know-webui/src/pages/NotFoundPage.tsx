import React from "react";
import { Link } from "react-router-dom";

export const NotFoundPage: React.FC = () => {
	return (
		<div className="py-24 text-center space-y-4">
			<h2 className="text-4xl font-bold text-slate-900 dark:text-slate-100">404</h2>
			<p className="text-slate-600 dark:text-slate-400 text-sm">The page you are looking for does not exist.</p>
			<div>
				<Link
					to="/coverage"
					className="inline-block px-5 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium rounded-lg transition-colors shadow-lg shadow-indigo-600/20"
				>
					Go to Coverage Matrix
				</Link>
			</div>
		</div>
	);
};
