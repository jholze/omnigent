package ai.omnigent.android

import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URI
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit

internal data class OAuthHttpRequest(
    val uri: URI,
    val method: String = "GET",
    val headers: Map<String, String> = emptyMap(),
    val body: ByteArray? = null,
)

internal data class OAuthHttpResponse(
    val uri: URI,
    val status: Int,
    val body: ByteArray,
    val contentType: String? = null,
)

internal fun interface OAuthTransport {
    fun execute(request: OAuthHttpRequest): OAuthHttpResponse
}

/** The request never got an HTTP response; callers report it as their own network error. */
internal class OAuthNetworkException(
    cause: Throwable? = null,
) : IOException(cause)

/**
 * Redirect-disabled, cookie-independent native OAuth transport. [timeoutMs] bounds the whole
 * exchange, and a response body larger than [maxBodyBytes] fails it.
 */
internal class UrlConnectionOAuthTransport(
    private val timeoutMs: Int = DEFAULT_TIMEOUT_MS,
    private val maxBodyBytes: Int = DEFAULT_MAX_BODY_BYTES,
) : OAuthTransport {
    override fun execute(request: OAuthHttpRequest): OAuthHttpResponse {
        val connection =
            try {
                request.uri.toURL().openConnection() as HttpURLConnection
            } catch (error: Exception) {
                throw OAuthNetworkException(error)
            }
        connection.instanceFollowRedirects = false
        connection.useCaches = false
        connection.connectTimeout = timeoutMs
        connection.readTimeout = timeoutMs
        // Socket timeouts bound each wait, not the exchange: a server trickling bytes could
        // otherwise hold the request open indefinitely.
        val deadlineAt = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeoutMs.toLong())
        val deadline =
            DEADLINES.schedule(
                { DISCONNECTS.execute(connection::disconnect) },
                timeoutMs.toLong(),
                TimeUnit.MILLISECONDS,
            )
        return try {
            connection.requestMethod = request.method
            request.headers.forEach(connection::setRequestProperty)
            request.body?.let { body ->
                connection.doOutput = true
                connection.setFixedLengthStreamingMode(body.size)
                connection.outputStream.use { it.write(body) }
            }
            val status = connection.responseCode
            val stream = if (status >= 400) connection.errorStream else connection.inputStream
            OAuthHttpResponse(
                uri = connection.url.toURI(),
                status = status,
                body = stream?.use { readBounded(it, deadlineAt) } ?: byteArrayOf(),
                contentType = connection.contentType,
            )
        } catch (error: Exception) {
            throw OAuthNetworkException(error)
        } finally {
            deadline.cancel(false)
            connection.disconnect()
        }
    }

    private fun readBounded(
        stream: InputStream,
        deadlineAt: Long,
    ): ByteArray {
        val body = ByteArrayOutputStream()
        val buffer = ByteArray(8 * 1024)
        while (true) {
            if (System.nanoTime() > deadlineAt) throw IOException("response exceeded its deadline")
            val read = stream.read(buffer)
            if (read < 0) return body.toByteArray()
            if (body.size() + read > maxBodyBytes) throw IOException("response body too large")
            body.write(buffer, 0, read)
        }
    }

    private companion object {
        const val DEFAULT_TIMEOUT_MS = 30_000
        const val DEFAULT_MAX_BODY_BYTES = 1024 * 1024

        /** Ends requests at their deadline. Daemon threads, so they never keep the app alive. */
        val DEADLINES: ScheduledExecutorService =
            Executors.newSingleThreadScheduledExecutor { task ->
                Thread(task, "oauth-request-deadline").apply { isDaemon = true }
            }

        /** A disconnect can wait on the connection, so it never blocks the deadline thread. */
        val DISCONNECTS: ExecutorService =
            Executors.newCachedThreadPool { task ->
                Thread(task, "oauth-request-disconnect").apply { isDaemon = true }
            }
    }
}
