<?php
/**
 * Plugin Name: WFH Connect — Rank Math REST Meta
 * Description: Registers the Rank Math SEO meta keys (rank_math_title, rank_math_description, rank_math_focus_keyword) for the WordPress REST API so the WFH content pipeline can set them when creating posts.
 * Author: The WFH Connect
 * Version: 1.0.0
 *
 * Install: copy this file to wp-content/mu-plugins/wfh-rest-meta.php
 * (create the mu-plugins folder if it does not exist). Must-use plugins are
 * always active — no activation step needed.
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

add_action( 'init', function () {
	$meta_keys = array(
		'rank_math_title',
		'rank_math_description',
		'rank_math_focus_keyword',
	);

	foreach ( $meta_keys as $meta_key ) {
		register_post_meta(
			'post',
			$meta_key,
			array(
				'show_in_rest'      => true,
				'single'            => true,
				'type'              => 'string',
				'sanitize_callback' => 'sanitize_text_field',
				'auth_callback'     => function () {
					return current_user_can( 'edit_posts' );
				},
			)
		);
	}
} );
