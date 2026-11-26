"""Transparent heuristic scoring. Not a prediction or investment recommendation."""

def score_token(token: dict) -> dict:
    security = 0
    flags = []
    if token.get('mint_authority') is False: security += 10
    elif token.get('mint_authority') is None: flags.append('Mint authority not verified')
    if token.get('freeze_authority') is False: security += 10
    elif token.get('freeze_authority') is None: flags.append('Freeze authority not verified')
    rug = token.get('rugcheck_score')
    if isinstance(rug, (int, float)):
        security += max(0, min(5, round(5 * (1 - min(rug, 100) / 100))))
    else: flags.append('RugCheck score unavailable')

    holders = token.get('top10_pct')
    holder_score = 15 if isinstance(holders, (int, float)) and holders < 20 else (8 if isinstance(holders, (int, float)) and holders < 35 else 0)
    if holders is None: flags.append('Top-holder concentration unavailable')

    smart = min(20, max(0, int(token.get('verified_smart_money_score', 0))))
    organic = min(15, max(0, int(token.get('organic_activity_score', 0))))
    narrative = min(15, max(0, int(token.get('narrative_score', 0))))
    graduation = min(10, max(0, int(token.get('graduation_score', 0))))
    score = security + smart + holder_score + organic + narrative + graduation
    critical = bool(token.get('critical_security_failure'))
    return {
        'total': 0 if critical else min(100, score),
        'components': {'security': security, 'smart_money': smart, 'holders': holder_score,
                       'organic_activity': organic, 'narrative': narrative, 'graduation': graduation},
        'flags': flags + (['Critical security failure: score overridden to 0'] if critical else []),
        'decision': 'BLOCK' if critical else ('REVIEW' if score >= 65 else 'LOW CONFIDENCE')
    }
