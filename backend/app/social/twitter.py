from __future__ import annotations

from app.config import settings
from app.social.base import PostResult, SocialPoster


class TwitterPoster(SocialPoster):
    """Posts to X via API v1.1 media upload (chunked for video) + v2 tweet creation."""

    platform = "twitter"

    def is_configured(self) -> bool:
        return bool(
            settings.twitter_api_key
            and settings.twitter_api_secret
            and settings.twitter_access_token
            and settings.twitter_access_secret
        )

    def _v1_api(self):
        import tweepy

        auth = tweepy.OAuth1UserHandler(
            settings.twitter_api_key,
            settings.twitter_api_secret,
            settings.twitter_access_token,
            settings.twitter_access_secret,
        )
        return tweepy.API(auth)

    def _v2_client(self):
        import tweepy

        return tweepy.Client(
            consumer_key=settings.twitter_api_key,
            consumer_secret=settings.twitter_api_secret,
            access_token=settings.twitter_access_token,
            access_token_secret=settings.twitter_access_secret,
        )

    def _upload_and_tweet(self, media_path: str, caption: str, chunked: bool) -> PostResult:
        media = self._v1_api().media_upload(media_path, chunked=chunked)
        response = self._v2_client().create_tweet(text=caption, media_ids=[media.media_id])
        tweet_id = response.data["id"]
        return PostResult(
            platform=self.platform,
            success=True,
            dry_run=False,
            post_id=str(tweet_id),
            url=f"https://x.com/i/web/status/{tweet_id}",
        )

    def _post_image(self, image_path: str, caption: str) -> PostResult:
        return self._upload_and_tweet(image_path, caption, chunked=False)

    def _post_video(self, video_path: str, caption: str) -> PostResult:
        return self._upload_and_tweet(video_path, caption, chunked=True)
