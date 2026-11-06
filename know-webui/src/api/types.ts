export type DomainState = "bootstrapped" | "missing" | "partial" | "absent_source";

export interface DomainStatus {
	state: DomainState;
	articles: number;
	expected_articles: number;
	questions: number;
	expected_questions: number;
	missing_uuids?: number;
}

export interface DomainEntry {
	slug: string;
	name: string;
	kind: string;
	status: Record<string, DomainStatus>;
}

export interface DomainsResponse {
	total: number;
	domains: DomainEntry[];
}

export interface CampaignEntry {
	slug: string;
	name: string;
	description: string;
	status: Record<string, { state: DomainState }>;
}

export interface CampaignsResponse {
	total: number;
	campaigns: CampaignEntry[];
}

export type CellStatus = "ok" | "thin" | "incomplete" | "absent" | "unknown";

export interface CampaignCellEntry {
	lang: string;
	domain: string;
	status: CellStatus;
	articles: number;
	expected_articles: number;
	questions: number;
	short_articles: number;
}

export interface CampaignDetailResponse {
	slug: string;
	name: string;
	description: string;
	order: number;
	domain_slugs: string[];
	prerequisite_domain_slugs: string[];
	prerequisite_campaign_slugs: string[];
	langs: string[];
	cells: CampaignCellEntry[];
}

export interface DomainDetailResponse {
	slug: string;
	name: string;
	description: string;
	kind: string;
	campaigns: string[];
	status: Record<string, DomainStatus>;
}

export interface PlanItem {
	slug: string;
	lang: string;
	action: "queue" | "skip";
	reason: string;
	state: DomainState;
	task_id?: string;
}

export interface TranslatePlanResponse {
	domains_matched: number;
	items: PlanItem[];
	summary: { queue: number; skip: number; blocked: number };
}

export type ArticleState = "ready" | "short" | "missing";

export interface ArticleEntry {
	slug: string;
	title: string;
	bytes: number;
	questions_count: number;
	order: number;
	state: ArticleState;
}

export interface ArticlesResponse {
	domain: string;
	lang: string;
	total: number;
	articles: ArticleEntry[];
}

export type QuestionState = "ready" | "broken" | "missing" | "archived";

export interface QuestionEntry {
	uuid: string;
	text: string;
	type: string;
	difficulty: number;
	archived: boolean;
	state: QuestionState;
	related_article_slug: string;
}

export interface QuestionsResponse {
	domain: string;
	lang: string;
	total: number;
	questions: QuestionEntry[];
}

export interface ArticleQuestionView {
	uuid: string;
	text: string;
	answer: string;
	distractors: Array<{ text: string; why?: string }>;
	hints: string[];
	difficulty: number;
	archived: boolean;
}

export interface ArticleDetailResponse {
	lang: string;
	domain: string;
	slug: string;
	title: string;
	summary: string;
	md_bytes: number;
	text: string;
	exists: boolean;
	short: boolean;
	questions: ArticleQuestionView[];
}

export interface QuestionAcrossLangView {
	lang: string;
	present: boolean;
	text: string;
	answer: string;
	distractors: Array<{ text: string; why?: string }>;
	hints: string[];
	difficulty: number;
}

export interface QuestionDetailResponse {
	uuid: string;
	domain_slug: string | null;
	article_slug: string | null;
	archived: boolean;
	languages: QuestionAcrossLangView[];
}

export interface UserSettingsProfile {
	username: string;
	active_domains: string[];
	active_langs: string[];
}

export interface GenerationParams {
	articles_per_domain: number;
	questions_types: { select: number; construct: number };
	per_domain?: Record<string, {
		articles_per_domain?: number;
		questions_types?: Partial<{ select: number; construct: number }>;
	}>;
	per_lang?: Record<string, {
		translate_domains?: "*" | string[];
	}>;
}

export interface UserSettings {
	profile: UserSettingsProfile;
	generation_params: GenerationParams;
	favorites: string[];
	pruned_favorites?: string[];
}

export interface UserSettingsPatch {
	active_domains?: string[];
	active_langs?: string[];
	generation_params?: Partial<GenerationParams>;
	favorites?: string[];
}

export interface SearchArticleItem {
	uuid: string;
	slug: string;
	domain_slug: string;
	lang: string;
	title: string;
	summary: string;
	snippet: string;
	rrf_score: number;
}

export interface SearchQuestionItem {
	uuid: string;
	domain_slug: string;
	lang: string;
	text: string;
	answer: string;
	snippet: string;
	rrf_score: number;
}

export interface SearchResponse {
	query: string;
	lang: string | null;
	semantic_available: boolean;
	elapsed_ms: number;
	articles: SearchArticleItem[];
	questions: SearchQuestionItem[];
}
