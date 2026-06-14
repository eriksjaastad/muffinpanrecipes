"""Publishing package — recipe-page rendering and catalog publishing.

The recipe-page renderer (episode_renderer) is the single source of truth
for recipe-page HTML. The legacy static-build PublishingPipeline was retired
once seed + cron recipes unified onto that renderer.
"""
