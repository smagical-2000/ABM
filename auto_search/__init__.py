"""Auto Search module — pre-account signal discovery pipeline.

This module ingests intent signals from external sources (layoffs, funding,
M&A, ACO contracts, leadership changes) and produces qualified candidate
companies for Galyna's review.

It is System A (discovery). It is NOT System B (post-campaign engagement).
The two systems share no tables. The only bridge is promotion: a Galyna
click that creates an `accounts` row from a `pending_companies` row.
"""
