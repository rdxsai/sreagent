"""Redaction helpers for public OpenTelemetry fixture files."""


BANNED_PUBLIC_TOKENS: tuple[str, ...] = (
    "paymentFailure",
    "paymentUnreachable",
    "recommendationCacheFailure",
    "productCatalogFailure",
    "adHighCpu",
    "adManualGc",
    "cartFailure",
    "kafkaQueueProblems",
    "loadGeneratorFloodHomepage",
    "imageSlowLoad",
    "emailMemoryLeak",
    "feature_flag.key",
    "feature_flag.result.variant",
    "feature_flag.result.value",
)
