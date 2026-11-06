import React from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Layout } from "./components/Layout";
import { LoginPage } from "./pages/LoginPage";
import { CoveragePage } from "./pages/CoveragePage";
import { DomainsPage } from "./pages/DomainsPage";
import { DomainDetailPage } from "./pages/DomainDetailPage";
import { CampaignsPage } from "./pages/CampaignsPage";
import { CampaignDetailPage } from "./pages/CampaignDetailPage";
import { ArticleDetailPage } from "./pages/ArticleDetailPage";
import { QuestionDetailPage } from "./pages/QuestionDetailPage";
import { SettingsPage } from "./pages/SettingsPage";
import { SearchPage } from "./pages/SearchPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { getToken } from "./lib/auth";

const RequireAuth = ({ children }: { children: React.ReactNode }) => {
	const token = getToken();
	if (!token) {
		return <Navigate to="/login" replace />;
	}
	return <>{children}</>;
};

const queryClient = new QueryClient({
	defaultOptions: {
		queries: {
			refetchOnWindowFocus: false,
			retry: 1,
		},
	},
});

export const App: React.FC = () => {
	return (
		<QueryClientProvider client={queryClient}>
			<BrowserRouter>
				<Routes>
					<Route path="/login" element={<LoginPage />} />
					<Route element={<RequireAuth><Layout /></RequireAuth>}>
						<Route path="/" element={<Navigate to="/coverage" replace />} />
						<Route path="/coverage" element={<CoveragePage />} />
						<Route path="/domains" element={<DomainsPage />} />
						<Route path="/domains/:slug" element={<DomainDetailPage />} />
						<Route path="/campaigns" element={<CampaignsPage />} />
						<Route path="/campaigns/:slug" element={<CampaignDetailPage />} />
						<Route path="/articles/:lang/:domain/:slug" element={<ArticleDetailPage />} />
						<Route path="/questions/:uuid" element={<QuestionDetailPage />} />
						<Route path="/search" element={<SearchPage />} />
						<Route path="/settings" element={<SettingsPage />} />
						<Route path="/settings/generation" element={<Navigate to="/settings" replace />} />
					</Route>
					<Route path="*" element={<NotFoundPage />} />
				</Routes>
			</BrowserRouter>
		</QueryClientProvider>
	);
};
