import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { adminGet, adminPost, adminPut } from "./client";
import {
	DomainsResponse,
	CampaignsResponse,
	TranslatePlanResponse,
	ArticlesResponse,
	QuestionsResponse,
	ArticleDetailResponse,
	QuestionDetailResponse,
	CampaignDetailResponse,
	DomainDetailResponse,
	GenerationParams,
	SearchResponse,
	UserSettings,
	UserSettingsPatch,
} from "./types";

export function useDomains(pattern?: string, langs?: readonly string[], onlyActive?: boolean) {
	const params = new URLSearchParams();
	if (pattern) params.set("pattern", pattern);
	if (langs && langs.length > 0) params.set("langs", langs.join(","));
	if (onlyActive) params.set("only_active", "true");
	const queryStr = params.toString() ? `?${params.toString()}` : "";

	return useQuery<DomainsResponse>({
		queryKey: ["domains", pattern, langs, onlyActive],
		queryFn: () => adminGet(`/v1/domains${queryStr}`),
		staleTime: 30_000,
		placeholderData: (prev) => prev,
	});
}

export function useCampaigns(pattern?: string, langs?: readonly string[]) {
	const params = new URLSearchParams();
	if (pattern) params.set("pattern", pattern);
	if (langs && langs.length > 0) params.set("langs", langs.join(","));
	const queryStr = params.toString() ? `?${params.toString()}` : "";

	return useQuery<CampaignsResponse>({
		queryKey: ["campaigns", pattern, langs],
		queryFn: () => adminGet(`/v1/campaigns${queryStr}`),
		staleTime: 30_000,
		placeholderData: (prev) => prev,
	});
}

export function useCampaignDetail(slug?: string, langs: readonly string[] = ["en"]) {
	const params = new URLSearchParams();
	if (langs.length) params.set("langs", langs.join(","));
	const queryStr = params.toString() ? `?${params.toString()}` : "";
	return useQuery<CampaignDetailResponse>({
		queryKey: ["campaign-detail", slug, langs],
		queryFn: () => adminGet(`/v1/campaigns/${slug}${queryStr}`),
		enabled: Boolean(slug),
		staleTime: 5000,
	});
}

export function useDomainDetail(slug?: string, langs: readonly string[] = ["en"]) {
	const params = new URLSearchParams();
	if (langs.length) params.set("langs", langs.join(","));
	const queryStr = params.toString() ? `?${params.toString()}` : "";
	return useQuery<DomainDetailResponse>({
		queryKey: ["domain-detail", slug, langs],
		queryFn: () => adminGet(`/v1/domains/${slug}${queryStr}`),
		enabled: Boolean(slug),
		staleTime: 5000,
	});
}

export interface TranslatePlanPayload {
	slug?: string;
	langs: string[];
}

export function useTranslatePlan(payload: TranslatePlanPayload, enabled = false) {
	return useQuery<TranslatePlanResponse>({
		queryKey: ["translate-plan", payload],
		queryFn: () => adminPost("/v1/domains/translate/plan", payload),
		enabled,
		retry: false,
	});
}

export interface TranslateEnqueuePayload {
	slug?: string;
	langs: string[];
}

// src/api/queries.ts (внутри useTranslateEnqueue)
export function useTranslateEnqueue() {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: (payload: TranslateEnqueuePayload) => adminPost("/v1/domains/translate/enqueue", payload),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ["domains"] });
			queryClient.invalidateQueries({ queryKey: ["campaigns"] });
			queryClient.invalidateQueries({ queryKey: ["domain-detail"] });
			queryClient.invalidateQueries({ queryKey: ["domain-articles"] });
			queryClient.invalidateQueries({ queryKey: ["domain-questions"] });
		},
	});
}

export function useDomainArticles(slug?: string, lang = "en", status?: string) {
	const params = new URLSearchParams();
	if (lang) params.set("lang", lang);
	if (status) params.set("status", status);
	const queryStr = params.toString() ? `?${params.toString()}` : "";

	return useQuery<ArticlesResponse>({
		queryKey: ["domain-articles", slug, lang, status],
		queryFn: () => adminGet(`/v1/domains/${slug}/articles${queryStr}`),
		enabled: Boolean(slug),
		staleTime: 5000,
	});
}

export function useDomainQuestions(slug?: string, lang = "en", articleSlug?: string, status?: string) {
	const params = new URLSearchParams();
	if (lang) params.set("lang", lang);
	if (articleSlug) params.set("article_slug", articleSlug);
	if (status) params.set("status", status);
	const queryStr = params.toString() ? `?${params.toString()}` : "";

	return useQuery<QuestionsResponse>({
		queryKey: ["domain-questions", slug, lang, articleSlug, status],
		queryFn: () => adminGet(`/v1/domains/${slug}/questions${queryStr}`),
		enabled: Boolean(slug),
		staleTime: 5000,
	});
}

export function useArticleDetail(lang?: string, domain?: string, slug?: string) {
	return useQuery<ArticleDetailResponse>({
		queryKey: ["article-detail", lang, domain, slug],
		queryFn: () => adminGet(`/v1/articles/${lang}/${domain}/${slug}`),
		enabled: Boolean(lang && domain && slug),
		staleTime: 5000,
	});
}

export function useQuestionDetail(uuid?: string) {
	return useQuery<QuestionDetailResponse>({
		queryKey: ["question-detail", uuid],
		queryFn: () => adminGet(`/v1/questions/${uuid}`),
		enabled: Boolean(uuid),
		staleTime: 5000,
	});
}

export function useGenerationParams() {
	return useQuery<GenerationParams>({
		queryKey: ["generation-params"],
		queryFn: () => adminGet("/v1/users/me/generation-params"),
		staleTime: 30_000,
	});
}

export function useUpdateGenerationParams() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: Partial<GenerationParams>) => adminPut("/v1/users/me/generation-params", body),
		onSuccess: () => {
			qc.invalidateQueries({ queryKey: ["generation-params"] });
			qc.invalidateQueries({ queryKey: ["domains"] });
			qc.invalidateQueries({ queryKey: ["domain-detail"] });
		},
	});
}

export function useUserSettings() {
	return useQuery<UserSettings>({
		queryKey: ["user-settings"],
		queryFn: () => adminGet("/v1/users/me/settings"),
		staleTime: 5_000,
	});
}

export function useUpdateUserSettings() {
	const qc = useQueryClient();
	return useMutation<UserSettings, Error, UserSettingsPatch>({
		mutationFn: (patch) => adminPut("/v1/users/me/settings", patch),
		onSuccess: (data) => {
			qc.setQueryData(["user-settings"], data);
			qc.invalidateQueries({ queryKey: ["domains"] });
			qc.invalidateQueries({ queryKey: ["domain-detail"] });
			qc.invalidateQueries({ queryKey: ["generation-params"] });
		},
	});
}

export function useSearch(q: string, lang?: string, type: string = "all") {
	const params = new URLSearchParams();
	if (q) params.set("q", q);
	if (lang) params.set("lang", lang);
	if (type) params.set("type", type);
	return useQuery<SearchResponse>({
		queryKey: ["search", q, lang, type],
		queryFn: () => adminGet(`/v1/search?${params}`),
		enabled: q.trim().length >= 2,
		staleTime: 30_000,
	});
}