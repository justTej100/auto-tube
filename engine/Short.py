

class Short:
    def __init__(self, gemini_api_key: str, hf_token: str | None, workdir: Path, voice_wav: Path, voice_mp3: Path, voice_converted: Path, google_client_id: str, google_client_secret: str, google_refresh_token: str, discord_webhook_url: str, discord_username: str | None = None):
      

        self.voice = Voice(voice_wav, voice_mp3, voice_converted)
        self.script = Script(gemini_api_key, hf_token, workdir, voice_wav, voice_mp3, voice_converted, google_client_id, google_client_secret, google_refresh_token, discord_webhook_url, discord_username)
        self.title = Title(gemini_api_key, hf_token, workdir, voice_wav, voice_mp3, voice_converted, google_client_id, google_client_secret, google_refresh_token, discord_webhook_url, discord_username)
        self.description = Description(gemini_api_key, hf_token, workdir, voice_wav, voice_mp3, voice_converted, google_client_id, google_client_secret, google_refresh_token, discord_webhook_url, discord_username)

        self.topic = topic # used for editing context



    def generate_short(self, topic: str):


    def upload_to_drive(self, file: Path):


    def __repr__(self):
        return f"Short(topic={self.topic})"

        





    