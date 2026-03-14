// Netlify serverless function: subscribe email via Resend Contacts API
exports.handler = async (event) => {
  const headers = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Content-Type": "application/json",
  };

  // Handle CORS preflight
  if (event.httpMethod === "OPTIONS") {
    return { statusCode: 204, headers, body: "" };
  }

  if (event.httpMethod !== "POST") {
    return {
      statusCode: 405,
      headers,
      body: JSON.stringify({ error: "Method not allowed" }),
    };
  }

  const RESEND_API_KEY = process.env.RESEND_API_KEY;
  const RESEND_AUDIENCE_ID = process.env.RESEND_AUDIENCE_ID;

  if (!RESEND_API_KEY || !RESEND_AUDIENCE_ID) {
    return {
      statusCode: 500,
      headers,
      body: JSON.stringify({ error: "Server misconfigured" }),
    };
  }

  let body;
  try {
    body = JSON.parse(event.body);
  } catch {
    return {
      statusCode: 400,
      headers,
      body: JSON.stringify({ error: "Invalid request body" }),
    };
  }

  const { email } = body;
  if (!email || !email.includes("@")) {
    return {
      statusCode: 400,
      headers,
      body: JSON.stringify({ error: "Valid email required" }),
    };
  }

  try {
    const res = await fetch(
      `https://api.resend.com/audiences/${RESEND_AUDIENCE_ID}/contacts`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${RESEND_API_KEY}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          email,
          unsubscribed: false,
        }),
      }
    );

    if (!res.ok) {
      const err = await res.json();
      return {
        statusCode: res.status,
        headers,
        body: JSON.stringify({
          error: err.message || "Failed to subscribe",
        }),
      };
    }

    return {
      statusCode: 200,
      headers,
      body: JSON.stringify({ message: "Subscribed successfully" }),
    };
  } catch (err) {
    return {
      statusCode: 500,
      headers,
      body: JSON.stringify({ error: "Internal server error" }),
    };
  }
};
