package com.yoursway.feedback.internal;

import static com.yoursway.feedback.internal.Join.join;

import java.io.IOException;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import com.yoursway.feedback.Detail;
import com.yoursway.feedback.FeedbackEngine;

public class FeedbackEngineImpl implements FeedbackEngine {

	private final FeedbackStorage storage;
	private final FeedbackCommunicator communicator;
	private final ClientInfoManager clientInfoManager;
	private final String productName;
	private final String productVersion;

	public FeedbackEngineImpl(String productName, String productVersion,
			FeedbackStorage storage, FeedbackCommunicator communicator) {
		if (productName == null)
			throw new NullPointerException("productName is null");
		if (productVersion == null)
			throw new NullPointerException("productVersion is null");
		if (storage == null)
			throw new NullPointerException("storage is null");
		if (communicator == null)
			throw new NullPointerException("communicator is null");
		this.productName = productName;
		this.productVersion = productVersion;
		this.storage = storage;
		this.communicator = communicator;
		this.clientInfoManager = new ClientInfoManager(storage, communicator);
		new FeedbackPostingThread().start();
	}

	private void report(Severity severity, Throwable cause,
			String userDescription, String developerDescription,
			Detail[] details) {
		Map<String, String> data = new HashMap<String, String>();
		encodeExceptionInfo(severity, cause, userDescription,
				developerDescription, data);
		if (cause == null)
			data.put("stack_trace", removeFirstLineOf(stackTraceOf(new RuntimeException(
					"Fake exception to collect stack trace at call site"))));
		data.put("problem_hash", YsDigest.sha1(RequestString.encode(data)));
		encodeOccurrenceInfo(cause, data, details);
		encodeEnvironmentInfo(data);
		storage.addReport(data);
		synchronized (this) {
			notifyAll();
		}
	}

	private static String removeFirstLineOf(String text) {
		int eol = text.indexOf('\n');
		if (eol < 0)
			return text;
		return text.substring(eol + 1);
	}

	private void encodeEnvironmentInfo(Map<String, String> data) {
		Map<String, String> env = collectEnvironmentInfo();
		copyWithPrefix(data, env, "env_");
		data.put("env_hash", YsDigest.sha1(RequestString.encode(env)));
	}

	private void encodeExceptionInfo(Severity severity, Throwable cause,
			String userDescription, String developerDescription,
			Map<String, String> data) {
		data.put("severity", severity.apiName);
		if (cause != null) {
			data.put("exception_names", join(",", exceptionNamesIn(cause)));
			data.put("stack_trace", stackTraceOf(cause));
			data.put("exception_messages", join("\n", messagesIn(cause)));
		}
		if (userDescription != null)
			data.put("user_message", userDescription);
		if (developerDescription != null)
			data.put("developer_message", developerDescription);
	}

	private static List<String> messagesIn(Throwable cause) {
		List<String> messages = new ArrayList<String>();
		Set<String> messageSet = new HashSet<String>();
		for (Throwable throwable = cause; throwable != null; throwable = throwable.getCause()) {
			String message = throwable.getMessage();
			if (message != null) {
				if (message.contains("\n"))
					message = message.substring(0, message.indexOf('\n'));
				if (message.length() > 1000)
					message = message.substring(0, 999) + "…";
				if (messageSet.add(message))
					messages.add(message);
			}
		}
		return messages;
	}

	private void encodeOccurrenceInfo(Throwable cause, Map<String, String> data, Detail[] details) {
		for (Throwable throwable = cause; throwable != null; throwable = throwable.getCause()) {
			try {
				Method method = throwable.getClass().getMethod("feedbackDetails");
				if (!Modifier.isStatic(method.getModifiers()) && Map.class.isAssignableFrom(method.getReturnType())) {
					Map<String, Object> map = (Map<String, Object>) method.invoke(throwable);
					if (map != null) {
						for (Map.Entry<String, Object> entry : map.entrySet())
							new Detail(entry.getKey(), entry.getValue()).addTo(this, data, "data_");
					}
				}
			} catch (Throwable e) {
			}
		}
		
		for (Detail detail : details)
			detail.addTo(this, data, "data_");
	}

	private static void copyWithPrefix(Map<String, String> target,
			Map<String, String> source, String prefix) {
		for (Map.Entry<String, String> entry : source.entrySet())
			target.put(prefix + entry.getKey(), entry.getValue());
	}

	private static List<String> exceptionNamesIn(Throwable cause) {
		List<String> exceptionNames = new ArrayList<String>();
		for (Throwable throwable = cause; throwable != null; throwable = throwable
				.getCause())
			exceptionNames.add(throwable.getClass().getName());
		return exceptionNames;
	}

	private static String stackTraceOf(Throwable throwable) {
		StringWriter sw = new StringWriter();
		throwable.printStackTrace(new PrintWriter(sw));
		String stackTrace = sw.toString();
		return stackTrace;
	}

	private Map<String, String> collectEnvironmentInfo() {
		Map<String, String> data = new HashMap<String, String>();
		putSystemProperty(data, "java_version", "java.version");
		data.put("product_name", productName);
		data.put("product_version", productVersion);
		putSystemProperty(data, "os_name", "os.name");
		putSystemProperty(data, "os_version", "os.version");
		putSystemProperty(data, "os_arch", "os.arch");
		putSystemProperty(data, "eclipse_build_id", "eclipse.buildId");
		putSystemProperty(data, "eclipse_product", "eclipse.product");
		putSystemProperty(data, "osgi_nl", "osgi.nl");
		putSystemProperty(data, "osgi_os", "osgi.os");
		putSystemProperty(data, "osgi_ws", "osgi.ws");
		data.put("cpu_count", Integer.toString(Runtime.getRuntime()
				.availableProcessors()));
		return data;
	}

	private void putSystemProperty(Map<String, String> data, String key,
			String property) {
		String value = System.getProperty(property);
		if (value != null && value.length() > 0)
			data.put(key, value);
	}

	public void majorBackgroundProcessingIssue(Throwable optionalCause,
			String userFriendlyDescription,
			String optionalDeveloperFriendlyDescription, Detail... details) {
		report(Severity.MAJOR_BACKGROUND, optionalCause,
				userFriendlyDescription, optionalDeveloperFriendlyDescription,
				details);
	}

	public void majorUserInitiatedActionIssue(Throwable optionalCause,
			String userFriendlyDescription,
			String optionalDeveloperFriendlyDescription, Detail... details) {
		report(Severity.MAJOR_USER_ACTION, optionalCause,
				userFriendlyDescription, optionalDeveloperFriendlyDescription,
				details);
	}

	public void minorVisualIssue(Throwable optionalCause,
			String userFriendlyDescription,
			String optionalDeveloperFriendlyDescription, Detail... details) {
		report(Severity.MINOR_VISUAL, optionalCause, userFriendlyDescription,
				optionalDeveloperFriendlyDescription, details);
	}

	public void silentRecovery(Throwable optionalCause,
			String developerFriendlyDescription, Detail... details) {
		report(Severity.SILENT_RECOVERY, optionalCause, null,
				developerFriendlyDescription, details);
	}

	class FeedbackPostingThread extends Thread {

		long lastFailureTimeOrMin = Long.MIN_VALUE;

		long lastEmptyQueueTimeOrMin = Long.MIN_VALUE;

		public FeedbackPostingThread() {
			setName(productName + " Bug Reporter");
			setDaemon(true);
		}

		@Override
		public void run() {
			Object master = FeedbackEngineImpl.this;
			while (true) {
				long now = System.currentTimeMillis();
				long minCheckTime = lastFailureTimeOrMin + 5000;
				if (now > minCheckTime) {
					try {
						PersistentReport report = storage.obtainReportToSend();
						if (report == null) {
							lastEmptyQueueTimeOrMin = now;
							System.out.println("No bug reports to send.");
						} else {
							lastEmptyQueueTimeOrMin = Long.MIN_VALUE;
							try {
								try {
								communicator.sendReport(
										clientInfoManager.get(), report.read());
								System.out.println("Report sent!");
								report.delete();
								lastFailureTimeOrMin = Long.MIN_VALUE;
								} catch (DesignatedCommunicationFailure e) {
									handleDesignatedFailure(e);
								}
							} catch (IOException e) {
								report.requeue();
								System.out
										.println("Failed sending bug report: "
												+ e.getMessage());
								lastFailureTimeOrMin = now;
							}
						}
					} catch (Throwable e) {
						e.printStackTrace(System.err);
					}
				}
				long nextCheck = Math.max(lastFailureTimeOrMin + 5000, Math
						.max(now, lastEmptyQueueTimeOrMin + 60000));
				long delay = nextCheck - now;
				if (delay > 0)
					synchronized (master) {
						try {
							master.wait(delay);
						} catch (InterruptedException e) {
						}
					}
			}
		}

	}

	public void handleDesignatedFailure(DesignatedCommunicationFailure e) {
		if (e.is("invalid-client-id"))
			clientInfoManager.reset();
	}

}
