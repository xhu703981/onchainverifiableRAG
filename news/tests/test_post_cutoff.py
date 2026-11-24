import calendar
import time

from newsreg.engine import post_cutoff

AS_OF = calendar.timegm(time.strptime("2026-11-24T15:32:28Z", "%Y-%m-%dT%H:%M:%SZ"))


def test_future_dates_abstain():
    for q in ["Who won the 2027 Super Bowl?", "What happened on December 5, 2026?", "What happened on Dec 3?",
              "What will happen tomorrow in Paris?", "Results of the January 2027 election", "What is planned for next week?"]:
        assert post_cutoff(q, AS_OF), q


def test_past_or_undated_pass():
    for q in ["Why did Anthropic's stock fall?", "What happened on November 20, 2026?", "What did NASA say in 2025?",
              "Who was arrested at the Windsor home?", "What happened in November 2026?"]:
        assert post_cutoff(q, AS_OF) is None, q
