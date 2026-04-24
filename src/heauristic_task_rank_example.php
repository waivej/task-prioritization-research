<?PHP

/*
 * Example version of heauristic_task_rank.php for public release.
 *
 * The scoring formula is intact and unmodified.
 * GetCustomerInvoiceCost() and GetCustomerInvoiceHosting() use illustrative
 * placeholder values — replace the keyword strings and cost amounts with your
 * own service catalog before deploying.
 *
 * This snippet shows how the WHMCS dashboard ranks tasks, used here as a
 * baseline against a Python improved formula and a Claude AI agent.
 */

while ($row = $query->fetch()) {
	$stats = getCustomerStats($row['userid'], $db);

	$age_weight = 12;
	$hourly_rate_weight = 4;
	$income_weight = 0.003;
	$hourly_rate = floor($stats["hourly"] / ($stats["hours"] + .001));
	if ($hourly_rate==0) $hourly_rate=50; // floor so low-activity clients still get a score
	if ($hourly_rate > 300) $hourly_rate=300; // cap to prevent rate explosion for clients with near-zero logged hours
	if ($hourly_rate > 60 and $row['age'] < 8) {
		$fresh_weight = 1000;
	} else {
		$fresh_weight = 0;
	}
	// invoice imminence: strongest empirical signal (corr=0.766 with 60-day revenue)
	$inv_due_soon = GetCustomerInvoiceDueSoon($row['userid'], 45, $db);
	$inv_signal = 7.0 * $inv_due_soon;
	// recency: exponential decay — clients worked on recently are more likely to pay
	$recency_bonus = 500 * exp(-$stats['idle'] / 14.0);
	$score = $age_weight * $row['age'] + ($hourly_rate_weight * $hourly_rate) + ($income_weight * $stats['income']) + $fresh_weight + $inv_signal + $recency_bonus;

	array_push($tasks, array('estimate' => $row['estimate'], 'id' => $row['id'], 'score' => $score, 'projectid' => $row['projectid'], 'project' => GetProjectName( $row['projectid'], $db), 'income' => $stats["income"], 'hourly_rate' => $hourly_rate, 'hours' => $stats['hours'], 'age' => $row["age"], 'task' => $row["task"], 'userid'=>$row['userid'], 'income'=>$stats['income']) );
	$found_some = True;
}


function getCustomerStats($id, $db) {
	if (false and !empty($_SESSION['customers'][$id])) {
		return $_SESSION['customers'][$id];
	} else {
		$maxdays = -600;

		$income  = floor(GetCustomerInvoiceTotal($id, $maxdays, $db));
		$cost    = floor(GetCustomerInvoiceCost($id, $maxdays, $db));
		$hosting = floor(GetCustomerInvoiceHosting($id, $maxdays, $db));
		$hourly  = $income - $hosting;
		$hours   = floor(GetCustomerTime($id, $maxdays, $db) * 10) / 10;
		$idle    = GetCustomerIdleTime($id, $db);
		$profit2 = floor($income - $cost - ($hours * $hourly));
		$_SESSION['customers'][$id] = array('income' => $income, 'hourly' => $hourly, 'cost' => $cost, 'hours' => $hours, 'idle' => $idle, 'profit2' => $profit2, 'hosting' => $hosting);

		return $_SESSION['customers'][$id];
	}
}


function GetCustomerInvoiceTotal($clientid, $maxdays, $db) {
	$query = $db->prepare("SELECT t.description, t.amount FROM `tblinvoiceitems` t INNER JOIN `tblinvoices` v ON t.invoiceid=v.id WHERE v.userid=:clientid AND datediff(v.duedate,now())>:maxdays and datediff(v.duedate,now())<45 and (v.status='Paid' or v.status='Unpaid')");
	$query->bindParam(":clientid", $clientid);
	$query->bindParam(":maxdays", $maxdays);
	$query->execute();

	$total = 0;
	while ($row = $query->fetch()) {
		$total += $row['amount'];
	}
	return $total;
}


function GetCustomerTime($clientid, $maxdays, $db) {
	$query = $db->prepare("SELECT sum(CAST(t.end AS DECIMAL) - CAST(t.start AS DECIMAL)) as total FROM `mod_projecttimes` as t JOIN `mod_project` as p ON p.id=t.projectid AND p.userid=:clientid AND not(t.end='' or t.end=0) AND datediff(from_unixtime(t.start),now())>:maxdays");
	$query->bindParam(":clientid", $clientid);
	$query->bindParam(":maxdays", $maxdays);
	$query->execute();

	while ($row = $query->fetch()) {
		if ($row['total'] > 0) return floor($row["total"] / 360) / 10;
	}
	return 0;
}


function GetCustomerInvoiceDueSoon($clientid, $days_ahead, $db) {
	$query = $db->prepare("SELECT sum(total) as total FROM `tblinvoices` WHERE userid=:clientid AND datediff(duedate,now())>=0 AND datediff(duedate,now())<=:days_ahead AND (status='Paid' OR status='Unpaid')");
	$query->bindParam(":clientid", $clientid);
	$query->bindParam(":days_ahead", $days_ahead);
	$query->execute();
	while ($row = $query->fetch()) {
		return $row["total"] ? $row["total"] : 0;
	}
	return 0;
}


/*
 * GetCustomerInvoiceCost — ILLUSTRATIVE PLACEHOLDERS
 *
 * Returns estimated wholesale cost for invoice line items in the billing window.
 * The keyword strings and cost amounts below are generic examples.
 * Replace with your actual service catalog and provider costs.
 */
function GetCustomerInvoiceCost($clientid, $maxdays, $db) {
	$query = $db->prepare("SELECT t.description, t.amount FROM `tblinvoiceitems` t INNER JOIN `tblinvoices` v ON t.invoiceid=v.id WHERE v.userid=:clientid AND datediff(v.duedate,now())>:maxdays and datediff(v.duedate,now())<45 and (v.status='Paid' or v.status='Unpaid')");
	$query->bindParam(":clientid", $clientid);
	$query->bindParam(":maxdays", $maxdays);
	$query->execute();

	$total = 0;
	while ($row = $query->fetch()) {
		if      (strpos($row['description'], 'Domain Renewal') !== FALSE)  $total += (0.85 * $row['amount']); // ~85% passthrough
		elseif  (strpos($row['description'], 'Site In Development') !== FALSE) $total += 0;
		elseif  (strpos($row['description'], 'Premium Managed Hosting') !== FALSE) $total += 50;  // illustrative flat rate
		elseif  (strpos($row['description'], 'Standard Hosting') !== FALSE) $total += 20;          // illustrative flat rate
		elseif  (strpos($row['description'], 'Basic Hosting') !== FALSE)    $total += 10;          // illustrative flat rate
		elseif  (strpos($row['description'], 'Free Website Hosting') !== FALSE) $total += 5;
		elseif  (strpos($row['description'], 'website forwarding') !== FALSE)   $total += 2;
		elseif  (strpos($row['description'], 'Reseller Hosting') !== FALSE) $total += $row['amount']; // full passthrough
		elseif  (strpos($row['description'], 'Hosted Exchange') !== FALSE)  $total += 20;          // illustrative flat rate
		elseif  (strpos($row['description'], 'Cloud Server') !== FALSE)     $total += $row['amount']; // full passthrough
		elseif  (strpos($row['description'], 'Email Hosting') !== FALSE)    $total += 5;           // illustrative flat rate
		elseif  (strpos($row['description'], 'DNS Hosting') !== FALSE)      $total += 2;           // illustrative flat rate
	}
	return $total;
}


/*
 * GetCustomerInvoiceHosting — ILLUSTRATIVE PLACEHOLDERS
 *
 * Returns total hosting revenue (used to isolate non-hosting/project revenue
 * for the implied hourly rate calculation). Keywords should match your own
 * service catalog. All matched items count their full invoice amount as hosting.
 */
function GetCustomerInvoiceHosting($clientid, $maxdays, $db) {
	$query = $db->prepare("SELECT t.description, t.amount FROM `tblinvoiceitems` t INNER JOIN `tblinvoices` v ON t.invoiceid=v.id WHERE v.userid=:clientid AND datediff(v.duedate,now())>:maxdays and datediff(v.duedate,now())<45 and (v.status='Paid' or v.status='Unpaid')");
	$query->bindParam(":clientid", $clientid);
	$query->bindParam(":maxdays", $maxdays);
	$query->execute();

	$total = 0;
	while ($row = $query->fetch()) {
		if      (strpos($row['description'], 'Domain Renewal') !== FALSE)       $total += $row['amount'];
		elseif  (strpos($row['description'], 'Site In Development') !== FALSE)  $total += $row['amount'];
		elseif  (strpos($row['description'], 'Premium Managed Hosting') !== FALSE) $total += $row['amount'];
		elseif  (strpos($row['description'], 'Standard Hosting') !== FALSE)     $total += $row['amount'];
		elseif  (strpos($row['description'], 'Basic Hosting') !== FALSE)        $total += $row['amount'];
		elseif  (strpos($row['description'], 'Free Website Hosting') !== FALSE) $total += $row['amount'];
		elseif  (strpos($row['description'], 'website forwarding') !== FALSE)   $total += $row['amount'];
		elseif  (strpos($row['description'], 'Reseller Hosting') !== FALSE)     $total += $row['amount'];
		elseif  (strpos($row['description'], 'Hosted Exchange') !== FALSE)      $total += $row['amount'];
		elseif  (strpos($row['description'], 'Cloud Server') !== FALSE)         $total += $row['amount'];
		elseif  (strpos($row['description'], 'Email Hosting') !== FALSE)        $total += $row['amount'];
		elseif  (strpos($row['description'], 'DNS Hosting') !== FALSE)          $total += $row['amount'];
	}
	return $total;
}
