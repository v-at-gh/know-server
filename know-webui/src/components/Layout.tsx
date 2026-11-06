import React from "react";
import { Outlet, NavLink, useNavigate } from "react-router-dom";
import { clearToken, getToken } from "../lib/auth";
import { useTheme, ThemePreference } from "../lib/theme";

export const Layout: React.FC = () => {
	const navigate = useNavigate();
	const token = getToken();
	const { preference, setPreference } = useTheme();

	const handleLogout = () => {
		clearToken();
		navigate("/login");
	};

	const themeOptions: Array<{ value: ThemePreference; label: string; icon: string }> = [
		{ value: "system", label: "System", icon: "💻" },
		{ value: "light", label: "Light", icon: "☀️" },
		{ value: "dark", label: "Dark", icon: "🌙" },
	];

	const navLinkClass = ({ isActive }: { isActive: boolean }) =>
		`flex items-center gap-3 px-4 py-2.5 rounded-lg text-sm font-medium transition-colors ${isActive
			? "bg-indigo-600 text-white shadow-lg shadow-indigo-600/20"
			: "text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200 hover:bg-slate-200/60 dark:hover:bg-slate-900"
		}`;

	return (
		<div className="min-h-screen bg-white dark:bg-slate-950 text-slate-800 dark:text-slate-200 flex">
			{/* Sidebar */}
			<aside className="w-64 bg-slate-50 dark:bg-slate-900 border-r border-slate-200 dark:border-slate-800 flex flex-col">
				<div className="p-6 border-b border-slate-200 dark:border-slate-800">
					<div className="flex items-center gap-3">
						<div className="w-8 h-8 rounded-lg bg-indigo-600 flex items-center justify-center font-bold text-white">
							K
						</div>
						<div>
							<h1 className="font-bold text-slate-900 dark:text-slate-100 tracking-tight">Know WebUI</h1>
							<p className="text-xs text-slate-500 dark:text-slate-400">Content Dashboard v1</p>
						</div>
					</div>
				</div>

				<nav className="flex-1 p-4 space-y-1.5">
					<NavLink to="/coverage" className={navLinkClass}>
						<span>Coverage Matrix</span>
					</NavLink>
					<NavLink to="/domains" className={navLinkClass}>
						<span>Domains</span>
					</NavLink>
					<NavLink to="/campaigns" className={navLinkClass}>
						<span>Campaigns</span>
					</NavLink>
					<NavLink to="/search" className={navLinkClass}>
						<span>Search</span>
					</NavLink>
					<NavLink to="/settings" className={navLinkClass}>
						<span>Settings</span>
					</NavLink>
				</nav>

				{token && (
					<div className="p-4 border-t border-slate-200 dark:border-slate-800">
						<button
							onClick={handleLogout}
							className="w-full flex items-center justify-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-rose-600 dark:text-rose-400 bg-rose-100 dark:bg-rose-950/40 border border-rose-300 dark:border-rose-900/60 hover:bg-rose-200 dark:hover:bg-rose-950/80 transition-colors"
						>
							Logout
						</button>
					</div>
				)}
			</aside>

			{/* Main Content Area */}
			<main className="flex-1 flex flex-col min-w-0 overflow-y-auto">
				<header className="h-16 border-b border-slate-200 dark:border-slate-800 bg-white/80 dark:bg-slate-900/50 backdrop-blur px-8 flex items-center justify-between sticky top-0 z-10">
					<div className="flex items-center gap-3">
						<span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-100 dark:bg-emerald-950 text-emerald-800 dark:text-emerald-400 border border-emerald-300 dark:border-emerald-800">
							<span className="w-2 h-2 rounded-full bg-emerald-500 dark:bg-emerald-400 animate-pulse" />
							Live (5s)
						</span>
					</div>

					<div className="flex items-center gap-4">
						<div className="flex items-center gap-1 bg-slate-100 dark:bg-slate-800/80 p-1 rounded-lg border border-slate-300 dark:border-slate-700/60">
							{themeOptions.map((opt) => (
								<button
									key={opt.value}
									onClick={() => setPreference(opt.value)}
									className={`px-2 py-1 text-xs rounded font-medium transition-colors flex items-center gap-1 ${
										preference === opt.value
											? "bg-indigo-600 text-white shadow"
											: "text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200 hover:bg-slate-200 dark:hover:bg-slate-700/50"
									}`}
									title={`Theme: ${opt.label}`}
								>
									<span>{opt.icon}</span>
									<span>{opt.label}</span>
								</button>
							))}
						</div>

						<div className="text-xs text-slate-500 dark:text-slate-400 font-mono">
							Port :8003 (Vite)
						</div>
					</div>
				</header>

				<div className="p-8 max-w-7xl w-full mx-auto flex-1">
					<Outlet />
				</div>
			</main>
		</div>
	);
};
