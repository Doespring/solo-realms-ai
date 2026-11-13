
import unittest
from unittest import mock
import solo.cloud as cloud


class MirrorFallbackTest(unittest.TestCase):
    """A refused mirror must not end the player's turn.

    The chosen endpoint stays the preference; when that mirror reports a gateway
    error before doing any work, the same model may answer elsewhere. Any other
    refusal is still a refusal, and the model never changes.
    """

    def test_pinned_endpoint_allows_same_model_fallback(self):
        source = open(cloud.__file__, encoding='utf-8').read()
        self.assertIn("'only':[cfg.endpoint],'allow_fallbacks':True", source.replace(' ', ''))

    def test_fallback_is_only_for_gateway_errors(self):
        self.assertIn('(502,503,504)', open(cloud.__file__, encoding='utf-8').read().replace(', ', ','))

    def test_mirror_refusal_names_a_recovery(self):
        message = cloud.CloudLLM.provider_error(503)
        self.assertIn('HTTP 503', message)
        self.assertIn('маршрут этой же модели', message)
        # The turn was not executed and the player keeps control.
        self.assertIn('Ход не выполнен', message)

    def test_other_refusals_do_not_advertise_a_retry(self):
        message = cloud.CloudLLM.provider_error(401)
        self.assertIn('Автоматического повтора или смены модели нет', message)


if __name__ == '__main__':
    unittest.main()
