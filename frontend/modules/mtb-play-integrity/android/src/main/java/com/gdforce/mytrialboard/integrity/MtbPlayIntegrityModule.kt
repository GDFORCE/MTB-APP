package com.gdforce.mytrialboard.integrity

import com.google.android.play.core.integrity.IntegrityManagerFactory
import com.google.android.play.core.integrity.IntegrityTokenRequest
import expo.modules.kotlin.Promise
import expo.modules.kotlin.exception.CodedException
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

/**
 * Play Integrity token production — the Android half of spec §3.3/§17.3.
 *
 * WHAT THIS IS FOR: everything else in the capture chain is the app vouching
 * for itself. The server issues a nonce, this app says "I took this photo,
 * here, now" — and a modified build on a rooted phone says exactly the same
 * thing, indistinguishably. Play Integrity is the OS vouching for the app
 * instead: Google signs a statement that this is the genuine published build
 * running on a real device, and the backend verifies that signature against
 * Google rather than trusting anything here.
 *
 * WHAT THIS MODULE DELIBERATELY DOES NOT DO:
 *
 *  - It does not interpret the token. The raw token goes to the server, which
 *    decodes it through Google's API. A client that parsed its own verdict and
 *    reported "I'm fine" would be exactly the self-attestation this replaces.
 *  - It does not generate the nonce. The nonce comes from the server's capture
 *    session, so the resulting token is bound to one specific capture and
 *    cannot be replayed against another.
 *  - It does not fall back to anything on failure. An unavailable provider
 *    produces an error the caller turns into "no attestation", which the
 *    server treats as REVIEW. Silently returning a placeholder would be the
 *    one bug that makes this whole control decorative.
 */
class MtbPlayIntegrityModule : Module() {
  override fun definition() = ModuleDefinition {
    Name("MtbPlayIntegrity")

    // Lets JS distinguish "this build has the native module" from "the request
    // failed", which are different things for the reviewer to know about.
    Function("isAvailable") { true }

    AsyncFunction("requestToken") { nonce: String, promise: Promise ->
      // Google's constraints: URL-safe, non-wrapping, 16-500 characters. The
      // server's nonce is 32 hex characters, which satisfies all three. Checked
      // here anyway so a future change to the server's nonce format fails loudly
      // rather than producing tokens Google silently rejects.
      if (nonce.length < MIN_NONCE_LENGTH || nonce.length > MAX_NONCE_LENGTH) {
        promise.reject(
          CodedException(
            "ERR_INVALID_NONCE",
            "nonce must be $MIN_NONCE_LENGTH-$MAX_NONCE_LENGTH characters, got ${nonce.length}",
            null
          )
        )
        return@AsyncFunction
      }
      if (!URL_SAFE.matches(nonce)) {
        promise.reject(
          CodedException("ERR_INVALID_NONCE", "nonce must be URL-safe and unpadded", null)
        )
        return@AsyncFunction
      }

      val context = appContext.reactContext?.applicationContext
      if (context == null) {
        promise.reject(CodedException("ERR_NO_CONTEXT", "no application context available", null))
        return@AsyncFunction
      }

      try {
        val manager = IntegrityManagerFactory.create(context)
        manager
          .requestIntegrityToken(IntegrityTokenRequest.builder().setNonce(nonce).build())
          .addOnSuccessListener { response -> promise.resolve(response.token()) }
          .addOnFailureListener { error ->
            // Play Services missing, app not recognized, network down, quota
            // exhausted — all reach here. The distinction that matters to the
            // reviewer is made server-side from the decoded verdict, not from
            // this message, so it carries no verdict of its own.
            promise.reject(
              CodedException(
                "ERR_INTEGRITY_REQUEST_FAILED",
                error.message ?: error::class.java.simpleName,
                error
              )
            )
          }
      } catch (error: Throwable) {
        promise.reject(
          CodedException("ERR_INTEGRITY_UNAVAILABLE", error.message ?: "integrity API unavailable", error)
        )
      }
    }
  }

  private companion object {
    const val MIN_NONCE_LENGTH = 16
    const val MAX_NONCE_LENGTH = 500
    val URL_SAFE = Regex("^[A-Za-z0-9_-]+$")
  }
}
