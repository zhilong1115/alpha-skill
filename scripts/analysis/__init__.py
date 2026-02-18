"""Analysis modules for the US Stock Trading system."""

from scripts.analysis.earnings_analyzer import analyze_earnings_transcript, compare_guidance, analyze_earnings_surprise
from scripts.analysis.filing_parser import parse_13f_xml, fetch_latest_13f
from scripts.analysis.regime_detector import detect_regime, get_regime_adjustment
from scripts.analysis.sentiment_scraper import scrape_reddit_mentions, compute_sentiment_score
from scripts.analysis.news_analyzer import get_recent_news, score_news_sentiment, generate_news_signals
from scripts.analysis.debate import create_bull_case, create_bear_case, resolve_debate

__all__ = [
    "analyze_earnings_transcript",
    "compare_guidance",
    "analyze_earnings_surprise",
    "parse_13f_xml",
    "fetch_latest_13f",
    "detect_regime",
    "get_regime_adjustment",
    "scrape_reddit_mentions",
    "compute_sentiment_score",
    "get_recent_news",
    "score_news_sentiment",
    "generate_news_signals",
    "create_bull_case",
    "create_bear_case",
    "resolve_debate",
]
