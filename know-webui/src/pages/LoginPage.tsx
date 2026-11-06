import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { login } from "../lib/auth";

export const LoginPage: React.FC = () => {
	const [username, setUsername] = useState("admin");
	const [password, setPassword] = useState("");
	const [error, setError] = useState<string | null>(null);
	const [loading, setLoading] = useState(false);
	const navigate = useNavigate();

	const handleSubmit = async (e: React.FormEvent) => {
		e.preventDefault();
		setError(null);
		setLoading(true);
		try {
			await login(username, password);
			navigate("/coverage");
		} catch (err: unknown) {
			if (err instanceof Error) {
				setError(err.message);
			} else {
				setError("Login failed");
			}
		} finally {
			setLoading(false);
		}
	};

	return (
		<div className="min-h-screen bg-slate-100 dark:bg-slate-950 flex items-center justify-center p-4">
			<div className="max-w-md w-full bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl p-8 shadow-2xl">
				<div className="text-center mb-8">
					<div className="w-12 h-12 rounded-xl bg-indigo-600 text-white font-bold text-xl flex items-center justify-center mx-auto mb-4 shadow-lg shadow-indigo-600/30">
						K
					</div>
					<h2 className="text-2xl font-bold text-slate-900 dark:text-slate-100 tracking-tight">Know WebUI</h2>
					<p className="text-sm text-slate-600 dark:text-slate-400 mt-1">Sign in to your content dashboard</p>
				</div>

				{error && (
					<div className="mb-6 p-3 rounded-lg bg-rose-100 dark:bg-rose-950/60 border border-rose-300 dark:border-rose-900/80 text-rose-800 dark:text-rose-300 text-sm text-center">
						{error}
					</div>
				)}

				<form onSubmit={handleSubmit} className="space-y-4">
					<div>
						<label className="block text-xs font-medium uppercase text-slate-600 dark:text-slate-400 mb-1.5 tracking-wider">
							Username
						</label>
						<input
							type="text"
							value={username}
							onChange={(e) => setUsername(e.target.value)}
							required
							className="w-full px-4 py-2.5 bg-slate-50 dark:bg-slate-950 border border-slate-300 dark:border-slate-800 rounded-lg text-slate-900 dark:text-slate-200 focus:outline-none focus:border-indigo-500 transition-colors text-sm shadow-sm"
							placeholder="admin"
						/>
					</div>

					<div>
						<label className="block text-xs font-medium uppercase text-slate-600 dark:text-slate-400 mb-1.5 tracking-wider">
							Password
						</label>
						<input
							type="password"
							value={password}
							onChange={(e) => setPassword(e.target.value)}
							required
							className="w-full px-4 py-2.5 bg-slate-50 dark:bg-slate-950 border border-slate-300 dark:border-slate-800 rounded-lg text-slate-900 dark:text-slate-200 focus:outline-none focus:border-indigo-500 transition-colors text-sm shadow-sm"
							placeholder="••••••••"
						/>
					</div>

					<button
						type="submit"
						disabled={loading}
						className="w-full mt-2 py-3 bg-indigo-600 hover:bg-indigo-500 text-white font-medium rounded-lg transition-colors shadow-lg shadow-indigo-600/20 disabled:opacity-50 flex items-center justify-center gap-2 text-sm"
					>
						{loading && <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />}
						Sign In
					</button>
				</form>
			</div>
		</div>
	);
};
